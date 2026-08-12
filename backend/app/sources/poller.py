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

from ..config import HTTP_TIMEOUT, HTTP_UA, POLL_MAX_PAGES, POLL_MAX_PER_RUN
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


def _cursor(source: str, category: str) -> int:
    with connect() as cx:
        row = cx.execute(
            "SELECT last_id FROM source_category_cursor "
            "WHERE source=? AND category=?", (source, category)).fetchone()
    try:
        return int(row["last_id"]) if row and row["last_id"] else 0
    except (TypeError, ValueError):
        return 0


def _save_cursor(source: str, category: str, last_id: int) -> None:
    with connect() as cx:
        cx.execute(
            "INSERT INTO source_category_cursor(source,category,last_id,polled_at) "
            "VALUES(?,?,?,datetime('now')) "
            "ON CONFLICT(source,category) DO UPDATE SET "
            "last_id=excluded.last_id, polled_at=datetime('now')",
            (source, category, str(last_id)))


def _profiles() -> list[dict[str, Any]]:
    # api.profiles() is the single resolution of stored profiles against the
    # code presets (filters.resolve_profiles). Reading the setting directly
    # here is what let a preset added or widened in filters.py never reach the
    # poller — the one place where it matters most, because this is what
    # decides whether a listing is stored at all. Local import: api imports
    # this module's package at load time.
    from ..api import profiles
    return [p for p in profiles() if p.get("enabled", True)]


async def _poll_category(source, adapter, key: str, category: str,
                         fetch: Fetch, limit: int,
                         profiles: list[dict[str, Any]]) -> dict[str, Any]:
    """One category of one source. Its cursor is its own.

    Every watermark decision below is scoped to `category`: `since`, `high`
    and `stalled_at` are locals of this call, and the only cursor written is
    (key, category). That isolation is the point — a namai id numbered below
    the sodybos watermark must still be ingested.
    """
    since = _cursor(key, category)
    created: list[dict[str, Any]] = []
    scanned = rejected = 0
    high = since
    fresh: dict[int, str] = {}
    pages_capped = False

    # Walk every page until one yields no new ids, or POLL_MAX_PAGES stops us.
    #
    # Do NOT reinstate the obvious optimisation of breaking out once
    # len(fresh) >= limit. It looks like it saves a pointless fetch and it
    # silently loses listings: list_ids returns ids DESCENDING, so page 2
    # holds strictly lower ids than page 1 — and the batch below is taken
    # from the OLDEST end. Stopping the walk early therefore picks a batch
    # off page 1 and then advances the watermark above every id the
    # unfetched pages held, so those listings are never offered again on any
    # future run. Simulated against namai's real shape (352 listings,
    # per_page=200, limit=40) it lost 152 listings — 43% of the category —
    # on the first sweep, while reporting status ok and pages_capped False.
    # List pages are cheap and this walk is already capped; correctness wins.
    for page in range(1, POLL_MAX_PAGES + 1):
        await asyncio.sleep(source.crawl_delay_s)
        status, html = await fetch(adapter.list_url(category, page))
        if status != 200:
            # Cursor untouched: this category is simply skipped this run.
            # `created` is a list on every path -- poll_source extends with it.
            return {"status": "error", "http_status": status,
                    "scanned": 0, "rejected": 0, "created": [],
                    "pages_capped": False, "stalled_at": None}
        page_ids = [i_u for i_u in adapter.list_ids(html) if i_u[0] > since]
        if not page_ids:
            break
        fresh.update(dict(page_ids))          # same id on two pages counts once
        if page == POLL_MAX_PAGES:
            pages_capped = True

    # ids arrive newest-first. Process the OLDEST new ones first so the cursor
    # advances contiguously: a batch bigger than `limit` is then caught up over
    # successive runs instead of having its tail skipped.
    batch = sorted(fresh.items())[:limit]

    # stalled_at marks the first id in this category's run that could not be
    # fully ingested (fetch failure or unparseable page). This category's
    # cursor may only advance across the unbroken run of successes *before*
    # that id — once something stalls, every id at or after it must be
    # retried on the next run, or a higher-id sibling processed later in the
    # same batch would silently carry the watermark past a listing that was
    # never actually ingested, losing it forever.
    #
    # Known limitation, not solved here: a listing that fails *permanently*
    # (e.g. deleted between the category page and the detail fetch, so it
    # 404s every run) stalls that category's cursor at that id indefinitely,
    # and everything above it is refetched on every run. Ingestion still
    # works — _insert's fingerprint check makes the repeats a no-op — but the
    # run does needless work. Fixing this needs per-id failure counts (give
    # up after N consecutive stalls), which is out of scope for this task.
    # It stalls one category only: the others keep advancing.
    stalled_at = None
    for listing_id, url in batch:
        await asyncio.sleep(source.crawl_delay_s)
        st, page_html = await fetch(url)
        if st != 200:
            if stalled_at is None:
                stalled_at = listing_id
            continue
        scanned += 1
        listing = adapter.parse_detail(page_html, url)
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

        listing["source_category"] = category
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
        _save_cursor(key, category, high)

    return {"status": "ok", "scanned": scanned, "rejected": rejected,
            "created": created, "pages_capped": pages_capped,
            "stalled_at": stalled_at}


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
    per_category: dict[str, Any] = {}
    created: list[dict[str, Any]] = []
    scanned = rejected = 0

    for category in adapter.CATEGORIES:
        # Caught per category, the way poll_all isolates per source. Letting
        # a later category's exception abort the whole pass would discard the
        # earlier categories' `created` list — and both callers (api.
        # ingest_poll, main.scheduled_poll) read only `created`, so those
        # listings would be inserted and their cursor advanced with the
        # notification never fired, and never re-offered afterwards.
        try:
            res = await _poll_category(source, adapter, key, category,
                                       fetch, limit, profiles)
        except reg.PolicyError:
            raise
        except Exception as exc:
            log.exception("%s/%s poll failed", key, category)
            res = {"status": "error", "error": str(exc)[:400],
                   "scanned": 0, "rejected": 0, "created": [],
                   "pages_capped": False, "stalled_at": None}
        per_category[category] = {k: v for k, v in res.items() if k != "created"}
        per_category[category]["created"] = len(res.get("created") or [])
        created.extend(res.get("created") or [])
        scanned += res.get("scanned", 0)
        rejected += res.get("rejected", 0)

    parts = ", ".join(f"{c}: {r['scanned']}" for c, r in per_category.items())
    detail = f"{len(created)} nauji; peržiūrėta {scanned} ({parts}); atmesta {rejected}"
    capped = [c for c, r in per_category.items() if r.get("pages_capped")]
    if capped:
        detail += f"; puslapių riba pasiekta: {', '.join(capped)}"
    stalls = [f"{c} ties {r['stalled_at']}" for c, r in per_category.items()
              if r.get("stalled_at") is not None]
    if stalls:
        detail += (f"; kursorius sustabdytas ({', '.join(stalls)})"
                   " — bus bandoma dar kartą")
    failed = [c for c, r in per_category.items() if r["status"] == "error"]
    if failed:
        detail += f"; nepavyko: {', '.join(failed)}"

    # A sweep in which no category was reachable fetched nothing at all, and
    # must not report itself healthy: /api/ingest/log surfaces the status
    # column, so "ok" with the failure buried in the detail text is how a
    # dead poller goes unnoticed. Partial failure stays "ok" — the categories
    # that did run really did ingest, and `categories` names the ones that
    # did not.
    status = "error" if failed and len(failed) == len(per_category) else "ok"
    log_refresh(key, status, detail, len(created), started)
    log.info("%s: %s", key, detail)
    return {"status": status, "created": created, "scanned": scanned,
            "rejected": rejected, "categories": per_category}


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
