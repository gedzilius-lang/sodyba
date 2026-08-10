"""The polling ingest path.

Only sources declared POLL in registry.py are reachable: assert_pollable runs
before any connection is opened, and it refuses unknown keys as well as
forbidden ones, so adding a portal without first reading its robots.txt fails
loudly rather than silently scraping it.

Listings enter exactly the same pipeline as email alerts — locate, filter,
dedupe, insert — so there is one scorer and one notion of identity.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx

from ..config import HTTP_TIMEOUT, HTTP_UA, POLL_MAX_PER_RUN
from ..db import connect, log_refresh
from . import registry as reg
from . import adapters
from .mailbox import _insert

log = logging.getLogger(__name__)

POLLED = ["rinka"]

Fetch = Callable[[str], Awaitable[tuple[int, str]]]


async def _http_fetch(url: str) -> tuple[int, str]:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT,
                                 headers={"User-Agent": HTTP_UA},
                                 follow_redirects=True) as cx:
        r = await cx.get(url)
        return r.status_code, r.text


def _cursor(source: str) -> int:
    with connect() as cx:
        row = cx.execute("SELECT last_id FROM source_cursor WHERE source=?",
                         (source,)).fetchone()
    try:
        return int(row["last_id"]) if row and row["last_id"] else 0
    except (TypeError, ValueError):
        return 0


def _save_cursor(source: str, last_id: int) -> None:
    with connect() as cx:
        cx.execute(
            "INSERT INTO source_cursor(source,last_id,polled_at) "
            "VALUES(?,?,datetime('now')) ON CONFLICT(source) DO UPDATE SET "
            "last_id=excluded.last_id, polled_at=datetime('now')",
            (source, str(last_id)))


def _profiles() -> list[dict[str, Any]]:
    from ..db import get_setting
    from ..filters import PRESETS
    return [p for p in (get_setting("filter_profiles") or PRESETS)
            if p.get("enabled", True)]


async def poll_source(key: str, fetch: Fetch | None = None,
                      limit: int = POLL_MAX_PER_RUN) -> dict[str, Any]:
    """One polling pass over one source. Raises PolicyError if not permitted."""
    source = reg.assert_pollable(key)          # before anything else
    adapter = adapters.get(key)
    if adapter is None:
        raise reg.PolicyError(f"„{key}“ leidžiamas, bet adapteris neparašytas")

    fetch = fetch or _http_fetch
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    profiles = _profiles()
    since = _cursor(key)
    created: list[dict[str, Any]] = []
    scanned = rejected = 0
    high = since

    try:
        status, html = await fetch(adapter.list_url())
        if status != 200:
            log_refresh(key, "error", f"sąrašo puslapis grąžino {status}", 0, started)
            return {"status": "error", "http_status": status}

        # ids arrive newest-first. Process the OLDEST new ones first so the
        # cursor advances contiguously: a batch bigger than `limit` is then
        # caught up over successive runs instead of having its tail skipped.
        fresh = sorted(i_u for i_u in adapter.list_ids(html) if i_u[0] > since)[:limit]

        # stalled_at marks the first id in this run that could not be fully
        # ingested (fetch failure or unparseable page). The cursor may only
        # advance across the unbroken run of successes *before* that id —
        # once something stalls, every id at or after it must be retried on
        # the next run, or a higher-id sibling processed later in the same
        # batch would silently carry the watermark past a listing that was
        # never actually ingested, losing it forever.
        #
        # Known limitation, not solved here: a listing that fails
        # *permanently* (e.g. deleted between the category page and the
        # detail fetch, so it 404s every run) stalls the cursor at that id
        # indefinitely, and everything above it is refetched on every run.
        # Ingestion still works — _insert's fingerprint check makes the
        # repeats a no-op — but the run does needless work. Fixing this
        # needs per-id failure counts (give up after N consecutive stalls),
        # which is out of scope for this task.
        stalled_at = None
        for listing_id, url in fresh:
            await asyncio.sleep(source.crawl_delay_s)
            st, page = await fetch(url)
            if st != 200:
                if stalled_at is None:
                    stalled_at = listing_id
                continue
            scanned += 1
            listing = adapter.parse_detail(page, url)
            if listing.get("price_eur") is None and listing.get("house_m2") is None:
                # Not actually a listing (parse failure, redirect, teaser
                # page) — a failure to ingest just like a bad HTTP status,
                # so it must not let the cursor pass it either.
                if stalled_at is None:
                    stalled_at = listing_id
                continue

            from ..advisor import assess_nature       # local import: avoids a cycle
            from ..filters import evaluate_all, MATCH, NEAR
            from ..dedupe import fingerprint

            listing["nature"] = assess_nature(listing)
            results = evaluate_all(listing, profiles)
            hits = [r.key for r in results if r.state == MATCH]
            nears = [r.key for r in results if r.state == NEAR]
            if not hits and not nears:
                rejected += 1
                if stalled_at is None:
                    high = listing_id
                continue
            misses = {r.key: [vars(m) for m in r.misses]
                      for r in results if r.state in (MATCH, NEAR)}
            ref = _insert(listing, hits or nears, fingerprint(listing),
                          "match" if hits else "near", misses)
            if ref and hits:
                created.append({"ref": ref, "profiles": hits, **{
                    k: listing.get(k) for k in
                    ("title", "municipality", "locality", "price_eur",
                     "house_m2", "plot_ares", "url", "source")}})
            if stalled_at is None:
                high = listing_id

        if high > since:
            _save_cursor(key, high)
    except reg.PolicyError:
        raise
    except Exception as exc:
        log.exception("%s poll failed", key)
        log_refresh(key, "error", str(exc)[:400], 0, started)
        return {"status": "error", "error": str(exc)}

    detail = f"{len(created)} nauji; peržiūrėta {scanned}; atmesta {rejected}"
    if stalled_at is not None:
        detail += f"; kursorius sustabdytas ties {stalled_at} — bus bandoma dar kartą"
    log_refresh(key, "ok", detail, len(created), started)
    log.info("%s: %s", key, detail)
    return {"status": "ok", "created": created,
            "scanned": scanned, "rejected": rejected}


async def poll_all(fetch: Fetch | None = None) -> dict[str, Any]:
    """Poll every source with an adapter. One failure does not stop the rest."""
    out: dict[str, Any] = {}
    for key in POLLED:
        try:
            out[key] = await poll_source(key, fetch=fetch)
        except Exception as exc:
            log.exception("%s poll raised", key)
            out[key] = {"status": "error", "error": str(exc)}
    return out
