"""Mailbox poller — the automated ingestion path.

The portals we may not crawl all run their own alert services with user-selected
filters. Point those alerts at a dedicated mailbox, and this poller turns them
into scored candidates without you touching anything.

Flow: IMAP fetch unseen -> name the portal from the sender -> split digests ->
parse -> locate and measure nature -> test against enabled filter profiles ->
dedupe -> insert -> notify -> mark seen.

A message no portal we know sent is left alone rather than parsed by guess:
the source key ends up on a stored row and is read back as fact.

Read-only apart from the \\Seen flag. Nothing is deleted.
"""
from __future__ import annotations
import email
import imaplib
import json
import logging
from datetime import datetime, timezone
from email.header import decode_header, make_header
from typing import Any, Callable

from ..config import (IMAP_HOST, IMAP_PORT, IMAP_USER, IMAP_PASSWORD,
                      IMAP_FOLDER, IMAP_MAX_PER_RUN, IMAP_MARK_SEEN)
from ..db import connect, log_refresh
from ..dedupe import fingerprint as _fingerprint, find_duplicate
from . import parsers

log = logging.getLogger(__name__)

# The IMAP connection is injected the way poller.py injects its fetcher: a
# zero-argument factory, defaulted inside poll_mailbox to the real thing. The
# factory returns a CONNECTED but NOT yet authenticated client, because login
# is a step this module must own — a refused login has to come back as a value
# poll_mailbox returns, and its error text has to pass through _redact.
Imap = Callable[[], imaplib.IMAP4]


def _imap_ssl() -> imaplib.IMAP4:
    return imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)


def configured() -> bool:
    return bool(IMAP_HOST and IMAP_USER and IMAP_PASSWORD)


REDACTED = "***"


def _redact(text: str) -> str:
    """Text with IMAP_PASSWORD removed, in both plain and IMAP-quoted form.

    imaplib sends LOGIN with the password quoted and backslash-escaped, and
    more than one of its error paths quotes the command back at the caller, so
    a server's refusal can arrive carrying the credential inside it. That
    string does not stay in the process: poll_mailbox returns it to the
    browser through /api/ingest/mailbox, which app.js prints in a toast, and
    writes it to refresh_log, where it would sit in the database in clear.

    So every path out of the except blocks below goes through here, and the
    traceback is deliberately not logged with it — log.exception would print
    the exception's own message verbatim as the last line and undo this.

    Not covered, and not needed: AUTHENTICATE's base64 form. imaplib.login()
    sends the credential literally, which is the only call this module makes.
    """
    pw = IMAP_PASSWORD
    if not pw:                       # never .replace("", ...) — it matches everywhere
        return text
    for form in (pw, pw.replace("\\", "\\\\").replace('"', '\\"')):
        text = text.replace(form, REDACTED)
    return text


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


def _profiles() -> list[dict[str, Any]]:
    # One resolution for every path — see api.profiles and
    # filters.resolve_profiles. Local import to avoid the cycle, exactly as
    # _insert imports settings below.
    from ..api import profiles
    return profiles()


def _next_ref(cx) -> str:
    n = cx.execute("SELECT COUNT(*) c FROM candidate").fetchone()["c"]
    return f"K{n + 1:03d}"


# Column list and parameter tuple must stay in lockstep — 24 bound parameters
# against 24 `?` placeholders, over 28 columns, with flags/scores/checks
# defaulted inline and `archived` fixed at 0. Count all three sides if you
# touch this. (Was 20/20 over 24 columns before listed_at, contact_phone and
# contact_email were added, and 23/23 over 27 before source_category.)
_INSERT_SQL = (
    "INSERT INTO candidate("
    "ref,source,url,title,municipality,locality,cadastral_no,price_eur,house_m2,"
    "plot_ares,auction_ends_at,listed_at,contact_phone,contact_email,"
    "flags_json,scores_json,costs_json,checks_json,"
    "notes,fingerprint,profiles_json,easting,northing,nature_json,"
    "match_state,misses_json,source_category,archived) "
    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'{}','{}',?,'{}',?,?,?,?,?,?,?,?,?,0)"
)


def _loads_obj(raw: str | None) -> dict[str, Any]:
    """Stored JSON object, or an empty one. Never raises on a malformed column."""
    try:
        v = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def _merge_profiles(raw: str | None, hits: list[str]) -> list[str]:
    """Union of the stored profile keys and the incoming ones, stored order first."""
    try:
        stored = json.loads(raw or "[]")
    except (TypeError, ValueError):
        stored = []
    out = [str(k) for k in stored] if isinstance(stored, list) else []
    for k in hits:
        if k not in out:
            out.append(k)
    return out


def _promote(cx, twin: dict[str, Any], listing: dict[str, Any],
             hits: list[str]) -> None:
    """Turn a stored near-miss row into the match a newcomer has proved it is.

    Dedupe's tolerances are far tighter than the near-miss ones (5% on price
    against 25%), so a listing that genuinely satisfies a profile routinely
    arrives after a slightly pricier near-miss of the same property. Merging
    it away hides a qualifying property behind a row the default view filters
    out, and no notification is ever sent — a silently lost listing.

    The promotion has to carry the newcomer's price with it. A row claiming
    `match` while showing the price that missed the ceiling states something
    that contradicts itself, and a confidently wrong number is worse than the
    near-miss it replaced.
    """
    price = listing.get("price_eur")
    costs = _loads_obj(twin.get("costs_json"))
    if price is None:
        price = twin.get("price_eur")     # nothing better on offer, keep what we have
    else:
        costs["purchase"] = price
    cx.execute(
        "UPDATE candidate SET match_state='match', misses_json='{}', profiles_json=?, "
        "price_eur=?, costs_json=?, updated_at=datetime('now') WHERE id=?",
        (json.dumps(_merge_profiles(twin.get("profiles_json"), hits),
                    ensure_ascii=False),
         price, json.dumps(costs), twin["id"]))


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
        siblings = [dict(r) for r in cx.execute(
            "SELECT id,ref,cadastral_no,municipality,locality,price_eur,house_m2,plot_ares,"
            "title,match_state,costs_json,profiles_json "
            # An archived row is a rejected row. Merging a live listing into one
            # makes the property unreachable in every default view: rejecting
            # one portal's version of a sodyba must not delete the property.
            # The parentheses are load-bearing — AND binds tighter than OR, so
            # without them archived rows still arrive via the municipality arm.
            "FROM candidate WHERE (municipality IS ? OR cadastral_no IS NOT NULL) "
            "AND archived = 0",
            (listing.get("municipality"),)).fetchall()]
        twin = find_duplicate(listing, siblings)
        if twin:
            # Same property from another route. Keep the first row and record enough
            # of the second that a wrong merge is visible rather than silent — a
            # different price or title in this line means the match was wrong.
            bits = [f"[dublikatas {listing.get('source') or '?'}]"]
            if listing.get("price_eur") is not None:
                bits.append(f"{listing['price_eur']:.0f} EUR")
            if listing.get("house_m2") is not None:
                bits.append(f"{listing['house_m2']:.0f} m2")
            if listing.get("plot_ares") is not None:
                bits.append(f"{listing['plot_ares']:.0f} a")
            if listing.get("title"):
                bits.append(listing["title"][:120])
            if listing.get("url"):
                bits.append(listing["url"])
            cx.execute(
                "UPDATE candidate SET notes = COALESCE(notes,'') || ?, "
                "updated_at=datetime('now') WHERE id=?",
                ("\n" + " · ".join(bits), twin["id"]))
            if state == "match" and twin["match_state"] == "near":
                _promote(cx, twin, listing, hits)
                # The caller decides what to notify with `if ref and hits:`, so returning
                # the twin's ref is what turns a promotion into a push. It cannot push
                # twice, but not because of the fingerprint check above: _promote leaves
                # `fingerprint` alone, so the row keeps the *near* listing's fingerprint
                # and a re-sent match does reach here. What stops the second push is that
                # twin["match_state"] is now "match", so this branch is no longer taken.
                # Do not remove that comparison on the assumption the fingerprint covers it.
                return twin["ref"]
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
            listing.get("listed_at"),
            listing.get("contact_phone"),
            listing.get("contact_email"),
            json.dumps(costs),
            (listing.get("raw") or "")[:4000],
            fp,
            json.dumps(hits, ensure_ascii=False),
            nature.get("easting"),
            nature.get("northing"),
            json.dumps(nature, ensure_ascii=False),
            state,
            json.dumps(misses or {}, ensure_ascii=False),
            # Absent for every path but the poller — the email and paste routes
            # know no category, and NULL says so. A default here would make
            # every alert-ingested row claim a provenance nobody observed.
            listing.get("source_category"),
        ))
    return ref


def _mark_seen(box: imaplib.IMAP4, mid: bytes) -> None:
    """Consume a message this run is finished with, if the operator allows it.

    Called for every message that was read and decided about — including one
    whose body held no listing and one from a sender we do not recognise. Both
    were handled; leaving them unseen would mean re-reading them on every run
    forever.

    Not called for a message that could not be fetched or that raised on the
    way through. Those are unfinished business, and SR_IMAP_MARK_SEEN=false
    exists precisely so a run that goes wrong consumes nothing.
    """
    if IMAP_MARK_SEEN:
        box.store(mid, "+FLAGS", "\\Seen")


def _ingest_message(parse: Callable[[str], dict[str, Any]], body: str,
                    profiles: list[dict[str, Any]]) -> tuple[int, int, list]:
    """One alert email's listings -> (scanned, rejected, notifiable new rows).

    `parse` is chosen by the caller from the message headers and is never
    re-guessed from the text; see parsers.source_for_alert.
    """
    scanned = rejected = 0
    created: list[dict[str, Any]] = []
    for chunk in parsers.split_listings(parsers.to_text(body)):
        listing = parse(chunk)
        listing["raw"] = chunk
        if listing.get("price_eur") is None and listing.get("house_m2") is None:
            continue  # not a listing — footer, banner, unsubscribe block
        # Counted here, not before the guard: "peržiūrėta 40" has to mean
        # forty listings were judged, not forty banner blocks were walked
        # past. poller.py counts the same way, and the two numbers appear
        # side by side in the ingest log.
        scanned += 1
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
    return scanned, rejected, created


async def poll_mailbox(imap: Imap | None = None) -> dict[str, Any]:
    """One polling pass. Safe to call concurrently with the UI.

    `imap` injects the connection exactly as poller.poll_source injects
    `fetch`: leave it out and this opens a real IMAP4_SSL to SR_IMAP_HOST and
    behaves as it always has.

    Counts in the result, and what each one means:
      scanned   listings judged — chunks that carried a price or a floor area
      rejected  of those, the ones no enabled profile matched or came near
      created   new rows worth pushing (a fresh match, or a promoted near miss)
      unknown   messages whose sender named no portal we have a parser for
      failed    messages that could not be fetched, or that raised while being
                read; both are left unseen and retried on the next run
    """
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not configured():
        log_refresh("mailbox", "skipped", "IMAP nesukonfigūruotas", 0, started)
        return {"status": "skipped", "reason": "IMAP not configured"}

    profiles = [p for p in _profiles() if p.get("enabled", True)]
    created: list[dict[str, Any]] = []
    scanned = rejected = unknown = failed = 0
    open_imap = imap or _imap_ssl

    try:
        box = open_imap()
        box.login(IMAP_USER, IMAP_PASSWORD)
        box.select(IMAP_FOLDER)
        typ, data = box.search(None, "UNSEEN")
    except Exception as exc:
        # Connect, login, select or search: nothing was read, so the failure
        # is the whole report. Redacted, and logged without the traceback,
        # because a refused LOGIN can carry the password in its own message.
        safe = _redact(str(exc))
        log.error("mailbox poll failed: %s", safe)
        log_refresh("mailbox", "error", safe[:400], 0, started)
        return {"status": "error", "error": safe}

    # SEARCH answers oldest-first, so the *tail* is the newest IMAP_MAX_PER_RUN
    # messages. Taking the head would leave a backlog permanently ahead of
    # today's alerts — the newest listing is the one still for sale.
    ids = (data[0].split() if typ == "OK" and data and data[0] else [])[-IMAP_MAX_PER_RUN:]

    for mid in ids:
        try:
            typ, raw = box.fetch(mid, "(BODY.PEEK[])")
            if typ != "OK" or not raw or not isinstance(raw[0], (tuple, list)):
                failed += 1
                continue
            msg = email.message_from_bytes(raw[0][1])
            source = parsers.source_for_alert(_decode(msg.get("From")),
                                              _decode(msg.get("Subject")))
            parse = parsers.parser_for(source) if source else None
            if parse is None:
                # No portal we know sent this. It is not stored under any
                # source at all: "manual" would claim a human pasted it, and
                # sniffing the body for a portal name would let any footer
                # link decide what a stored row says about its own origin.
                unknown += 1
                _mark_seen(box, mid)
                continue
            s, r, c = _ingest_message(parse, _body(msg), profiles)
            scanned += s
            rejected += r
            created.extend(c)
            _mark_seen(box, mid)
        except Exception as exc:
            # One unreadable message must cost neither the messages after it
            # nor the rows already written. Left unseen on purpose: a message
            # that broke us is the evidence for fixing it.
            failed += 1
            log.warning("mailbox: message skipped: %s", _redact(str(exc)))

    try:
        box.close()
        box.logout()
    except Exception as exc:
        # The pass is over and its rows are committed. Failing to hang up
        # politely is not a reason to report the run as an error and throw
        # the created list away.
        log.warning("mailbox: closing failed: %s", _redact(str(exc)))

    detail = f"{len(created)} nauji; peržiūrėta {scanned}; atmesta pagal filtrus {rejected}"
    if unknown:
        detail += f"; nežinomų siuntėjų {unknown}"
    if failed:
        detail += f"; neperskaityta laiškų {failed}"
    log_refresh("mailbox", "ok", detail, len(created), started)
    log.info("mailbox: %s", detail)
    return {"status": "ok", "created": created, "scanned": scanned,
            "rejected": rejected, "unknown": unknown, "failed": failed}
