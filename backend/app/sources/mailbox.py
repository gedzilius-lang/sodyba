"""Mailbox poller — the automated ingestion path.

The portals we may not crawl all run their own alert services with user-selected
filters. Point those alerts at a dedicated mailbox, and this poller turns them
into scored candidates without you touching anything.

Flow: IMAP fetch unseen -> route to a portal parser -> split digests ->
locate and measure nature -> test against enabled filter profiles -> dedupe ->
insert -> notify -> mark seen.

Read-only apart from the \\Seen flag. Nothing is deleted.
"""
from __future__ import annotations
import email
import hashlib
import imaplib
import json
import logging
import re
from datetime import datetime, timezone
from email.header import decode_header, make_header
from typing import Any

from ..config import (IMAP_HOST, IMAP_PORT, IMAP_USER, IMAP_PASSWORD,
                      IMAP_FOLDER, IMAP_MAX_PER_RUN, IMAP_MARK_SEEN)
from ..db import connect, log_refresh
from . import parsers

log = logging.getLogger(__name__)


def configured() -> bool:
    return bool(IMAP_HOST and IMAP_USER and IMAP_PASSWORD)


def _decode(v: str | None) -> str:
    if not v:
        return ""
    try:
        return str(make_header(decode_header(v)))
    except Exception:
        return v


def _body(msg: email.message.Message) -> str:
    """Prefer text/html (portals put the structure there), fall back to plain."""
    best = {"text/html": None, "text/plain": None}
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct in best and "attachment" not in str(part.get("Content-Disposition", "")):
                try:
                    payload = part.get_payload(decode=True) or b""
                    best[ct] = best[ct] or payload.decode(
                        part.get_content_charset() or "utf-8", errors="replace")
                except Exception:
                    continue
    else:
        try:
            best[msg.get_content_type()] = (msg.get_payload(decode=True) or b"").decode(
                msg.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            pass
    return best["text/html"] or best["text/plain"] or ""


def _fingerprint(listing: dict[str, Any]) -> str:
    """Stable identity so a re-sent digest does not create duplicates."""
    if listing.get("url"):
        base = re.sub(r"[?#].*$", "", listing["url"])
    else:
        base = "|".join(str(listing.get(k) or "") for k in
                        ("source", "municipality", "locality", "price_eur", "house_m2"))
    return hashlib.sha1(base.encode()).hexdigest()[:16]


def _profiles() -> list[dict[str, Any]]:
    from ..db import get_setting
    from ..filters import PRESETS
    return get_setting("filter_profiles") or PRESETS


def _next_ref(cx) -> str:
    n = cx.execute("SELECT COUNT(*) c FROM candidate").fetchone()["c"]
    return f"K{n + 1:03d}"


# Column list and parameter tuple must stay in lockstep — 20 bound parameters
# against 20 `?` placeholders, with flags/scores/checks defaulted inline and
# `archived` fixed at 0. Count both sides if you touch this.
_INSERT_SQL = (
    "INSERT INTO candidate("
    "ref,source,url,title,municipality,locality,cadastral_no,price_eur,house_m2,"
    "plot_ares,auction_ends_at,flags_json,scores_json,costs_json,checks_json,"
    "notes,fingerprint,profiles_json,easting,northing,nature_json,"
    "match_state,misses_json,archived) "
    "VALUES(?,?,?,?,?,?,?,?,?,?,?,'{}','{}',?,'{}',?,?,?,?,?,?,?,?,0)"
)


def _insert(listing: dict[str, Any], hits: list[str], fp: str,
            state: str = "match", misses: dict[str, Any] | None = None) -> str | None:
    from ..api import settings
    costs = dict(settings()["auto_costs"])
    if listing.get("price_eur"):
        costs["purchase"] = listing["price_eur"]
    nature = listing.get("nature") or {}
    with connect() as cx:
        if cx.execute("SELECT 1 FROM candidate WHERE fingerprint=?", (fp,)).fetchone():
            return None
        ref = _next_ref(cx)
        cx.execute(_INSERT_SQL, (
            ref,
            listing.get("source") or "manual",
            listing.get("url"),
            listing.get("title"),
            listing.get("municipality"),
            listing.get("locality"),
            listing.get("cadastral_no"),
            listing.get("price_eur"),
            listing.get("house_m2"),
            listing.get("plot_ares"),
            listing.get("auction_ends_at"),
            json.dumps(costs),
            (listing.get("raw") or "")[:4000],
            fp,
            json.dumps(hits, ensure_ascii=False),
            nature.get("easting"),
            nature.get("northing"),
            json.dumps(nature, ensure_ascii=False),
            state,
            json.dumps(misses or {}, ensure_ascii=False),
        ))
    return ref


async def poll_mailbox() -> dict[str, Any]:
    """One polling pass. Safe to call concurrently with the UI."""
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not configured():
        log_refresh("mailbox", "skipped", "IMAP nesukonfigūruotas", 0, started)
        return {"status": "skipped", "reason": "IMAP not configured"}

    profiles = [p for p in _profiles() if p.get("enabled", True)]
    created: list[dict[str, Any]] = []
    scanned = rejected = 0

    try:
        box = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        box.login(IMAP_USER, IMAP_PASSWORD)
        box.select(IMAP_FOLDER)
        typ, data = box.search(None, "UNSEEN")
        ids = (data[0].split() if typ == "OK" and data and data[0] else [])[-IMAP_MAX_PER_RUN:]

        for mid in ids:
            typ, raw = box.fetch(mid, "(BODY.PEEK[])")
            if typ != "OK" or not raw or not raw[0]:
                continue
            msg = email.message_from_bytes(raw[0][1])
            sender = _decode(msg.get("From"))
            subject = _decode(msg.get("Subject"))
            body = _body(msg)
            if not body:
                continue

            text = parsers.to_text(body)
            for chunk in parsers.split_listings(text):
                scanned += 1
                listing = parsers.route(sender, subject, chunk)
                listing["raw"] = chunk
                if listing.get("price_eur") is None and listing.get("house_m2") is None:
                    continue  # not a listing — footer, banner, unsubscribe block
                # Locate before filtering so profiles can gate on measured
                # distance to water rather than on whatever the advert claims.
                from ..advisor import assess_nature  # local import: avoids a cycle
                listing["nature"] = assess_nature(listing)
                from ..filters import evaluate_all, MATCH, NEAR
                results = evaluate_all(listing, profiles)
                hits = [r.key for r in results if r.state == MATCH]
                nears = [r for r in results if r.state == NEAR]
                if not hits and not nears:
                    rejected += 1
                    continue
                state = "match" if hits else "near"
                misses = {r.key: [vars(m) for m in r.misses]
                          for r in results if r.state in (MATCH, NEAR)}
                ref = _insert(listing, hits or [r.key for r in nears],
                              _fingerprint(listing), state, misses)
                if ref and hits:
                    created.append({"ref": ref, "profiles": hits, **{
                        k: listing.get(k) for k in
                        ("title", "municipality", "locality", "price_eur",
                         "house_m2", "plot_ares", "url", "source")}})

            if IMAP_MARK_SEEN:
                box.store(mid, "+FLAGS", "\\Seen")

        box.close()
        box.logout()
    except Exception as exc:
        log.exception("mailbox poll failed")
        log_refresh("mailbox", "error", str(exc)[:400], 0, started)
        return {"status": "error", "error": str(exc)}

    detail = f"{len(created)} nauji; peržiūrėta {scanned}; atmesta pagal filtrus {rejected}"
    log_refresh("mailbox", "ok", detail, len(created), started)
    log.info("mailbox: %s", detail)
    return {"status": "ok", "created": created, "scanned": scanned, "rejected": rejected}
