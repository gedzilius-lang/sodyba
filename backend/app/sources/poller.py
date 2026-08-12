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


def _is_category_page(adapter: Any, html: str) -> bool:
    """Whether the source's own category listing answered, per the adapter.

    Only a page this is true of may end the walk on zero results. Adapters
    that do not answer keep the old proxy — any listing link means it was a
    page — so adding this cannot change their behaviour.
    """
    fn = getattr(adapter, "is_category_page", None)
    return bool(fn(html)) if fn else bool(adapter.list_ids(html))


def _results_bounded(adapter: Any, html: str) -> bool:
    """Whether the adapter could isolate this category's own results.

    Adapters that do not answer are assumed to have done so: they are not
    known to carry a foreign block, and warning about every page of every
    other source would be noise, not signal.
    """
    fn = getattr(adapter, "results_bounded", None)
    return bool(fn(html)) if fn else True


def _is_the_listing(listing: dict[str, Any], listing_id: int) -> bool:
    """Whether the page the adapter read declares itself to be advert `listing_id`.

    This is the test for "did we actually get the listing", and it is asked of
    the page's own identity, never of the fields parsed out of it.

    THE RULE THIS REPLACED WAS BACKWARDS. It was `price_eur is None and
    house_m2 is None` -> not a listing, and both halves of it were wrong,
    measured 2026-08-13 against the live site:

      * id 4992805 is a real sodybos advert -- an abandoned homestead, 33 ares
        in Rokiskio rajono, listed 2023-01-15, the operator's thesis exactly.
        It names no price ("Kaina: 1,00 EUR", a nominal placeholder) and no
        floor area, so the rule called it a parse failure. Its cursor stalled
        there and no sodybos listing newer than it had EVER been ingested: the
        watermark sat at 4991510 while the category's newest was 5080920.
      * rinka's 404 body -- a genuine non-listing, the case the guard exists
        for -- renders the site-wide newest-adverts block, so parsing it whole
        yields price 215000 EUR, 104.42 m2, 25.13 a and a municipality. The
        rule PASSED it. A fuller row than the real listing it refused.

    So no rule counting populated fields can work here: the error page scores
    higher than the homestead. Optional fields describe a property; they
    cannot answer whether a property was described at all.

    What can: the advert's own identity. Every real rinka detail page declares
    which advert it is, at document level, and no non-advert page does -- see
    adapters/rinka.declared_listing_id for the two landmarks and the pages
    they were measured against. Comparing that to the id we asked for also
    closes the redirect hole the old guard was written for and did not close:
    a page that is a perfectly good advert, but a DIFFERENT one, is refused
    rather than stored under the requested listing's URL.

    An adapter that reports no identity fails this for every listing. That is
    deliberate -- silence must not read as consent when the answer decides
    whether a page enters the pipeline -- and it is loud: nothing ingests, the
    run's log line names the ids, and they land in poll_failure. Reporting
    `listing_id` is part of the adapter contract, see AGENT.md section 9.
    """
    declared = listing.get("listing_id")
    try:
        return declared is not None and int(declared) == listing_id
    except (TypeError, ValueError):
        return False


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

    # Walk the category page by page. What the pages actually look like,
    # measured against the live site 2026-08-12 (per_page=200):
    #
    #   sodybos  page 1: 96 ids   page 2: 10 ids   page 3: the same 10 ids
    #   namai    page 1: 208 ids  page 2: 162 ids  page 3: 10 ids
    #
    # Two facts fall out of that, and this loop rests on both:
    #
    # 1. The category's own ids DESCEND across pages — page 2 holds lower
    #    ids than page 1. Until namai was added this was never exercised:
    #    sodybos is 86 listings, one page, so the walk never had a second
    #    page to be wrong about. namai (~350) is the first category that
    #    genuinely paginates.
    # 2. Every page — including one past the end of the category — also
    #    renders a fixed block of the ~10 newest listings on the site. So a
    #    page past the end is NOT empty, and it is not even lower-numbered:
    #    that block is the TOP of the id range. Testing `if not page_ids`
    #    against it never fires, which used to walk every category to
    #    POLL_MAX_PAGES on every run.
    #
    # Hence the end-of-category signal is "this page added no ids we did not
    # already have", not "this page was empty" and not "this page was all
    # below the cursor" (which is the same thing once `since` filtering has
    # run). A page carrying NO listing links at all is a different animal
    # entirely and is handled as a stall below.
    #
    # Do NOT reinstate the obvious optimisation of breaking out once
    # len(fresh) >= limit. It looks like it saves a pointless fetch and it
    # silently loses listings: the batch below is taken from the OLDEST end,
    # so stopping the walk early picks a batch off page 1 and then advances
    # the watermark above every id the unfetched pages held, and those are
    # never offered again on any future run. Simulated against namai's real
    # shape (352 listings, per_page=200, limit=40) it lost 152 listings —
    # 43% of the category — on the first sweep, reporting status ok. List
    # pages are cheap and this walk is already capped; correctness wins.
    listing_free_page = None
    for page in range(1, POLL_MAX_PAGES + 1):
        await asyncio.sleep(source.crawl_delay_s)
        status, html = await fetch(adapter.list_url(category, page))
        if status != 200:
            # Cursor untouched: this category is simply skipped this run.
            # `created` is a list on every path -- poll_source extends with it.
            return {"status": "error", "http_status": status,
                    "scanned": 0, "rejected": 0, "created": [],
                    "pages_capped": False, "stalled_at": None,
                    "listing_free_page": None}
        if not _results_bounded(adapter, html):
            # The adapter could not tell this category's own results from the
            # site-wide newest-adverts block, so it read the whole page. Every
            # id still reaches us — nothing is lost — but some may carry a
            # category they did not come from. PURE adapters report; callers
            # log, as main.py does for registry.stale().
            log.warning("%s/%s page %s: could not bound the results block, "
                        "reading every id on the page", key, category, page)
        all_ids = adapter.list_ids(html)
        if not all_ids and not _is_category_page(adapter, html):
            # Zero results, and the source's own category listing is not what
            # answered: a rate limiter, a maintenance notice, or an error page
            # rendered inside the site's chrome, wearing a 200. Reading that
            # as the end of the category would advance the watermark over
            # every lower id on the pages behind it, so it is a stall.
            #
            # The test has to be this positive landmark, not "the page had
            # some listing markup". Measured 2026-08-13, rinka renders a
            # missing category inside the full layout with its newest-adverts
            # block intact, so listing markup is present on pages that carry
            # no results at all. And `not all_ids` alone will not do either:
            # a page past the end of a category legitimately yields zero ids
            # now that the newest block is excluded, and that is the ordinary
            # end of the walk.
            listing_free_page = page
            break
        before = len(fresh)
        fresh.update({i: u for i, u in all_ids if i > since})
        if len(fresh) == before:
            break                    # nothing new here: end of the category
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

    if (pages_capped or listing_free_page is not None) and fresh:
        # Both exits stopped the walk part-way down a descending list
        # without proving where the category ends, so neither may advance
        # the watermark — that is the trap the early stop above fell into.
        # The pages never reached hold ids BELOW everything in `batch`, so
        # contiguity from `since` upward cannot be established: this
        # category's true floor is on a page the run never saw.
        #
        # Rather than a second rule, say that in the one the loop already
        # enforces: the run stalled at the lowest id it did fetch, so `high`
        # never leaves `since` and every id here is offered again next run.
        # The listings are still ingested below — _insert is idempotent
        # through its fingerprint check, so there is no reason to throw that
        # work away; only the watermark is withheld.
        #
        # A category whose fresh backlog permanently exceeds POLL_MAX_PAGES x
        # per_page therefore never advances at all. That is the right trade —
        # standing still beats losing listings — but it must be loud, not
        # silent, which is why poll_source's log line names the setting to
        # raise rather than only reporting that the cap was reached.
        stalled_at = min(fresh)

    for listing_id, url in batch:
        await asyncio.sleep(source.crawl_delay_s)
        st, page_html = await fetch(url)
        if st != 200:
            if stalled_at is None:
                stalled_at = listing_id
            continue
        scanned += 1
        listing = adapter.parse_detail(page_html, url)
        if not _is_the_listing(listing, listing_id):
            # The site answered 200 with something that is not this advert
            # (redirect, 404 body, teaser, error page inside the chrome) — a
            # failure to ingest just like a bad HTTP status, so it must not
            # let the cursor pass it either. See _is_the_listing.
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
            "stalled_at": stalled_at, "listing_free_page": listing_free_page}


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
                   "pages_capped": False, "stalled_at": None,
                   "listing_free_page": None}
        per_category[category] = {k: v for k, v in res.items() if k != "created"}
        per_category[category]["created"] = len(res.get("created") or [])
        created.extend(res.get("created") or [])
        scanned += res.get("scanned", 0)
        rejected += res.get("rejected", 0)

    parts = ", ".join(f"{c}: {r['scanned']}" for c, r in per_category.items())
    detail = f"{len(created)} nauji; peržiūrėta {scanned} ({parts}); atmesta {rejected}"
    capped = [c for c, r in per_category.items() if r.get("pages_capped")]
    if capped:
        # The cap is a safety net sized well above any real category, so
        # reaching it means something upstream is wrong — and a capped
        # category cannot advance its cursor, so it will keep re-fetching the
        # same head every run until an operator raises the setting. Say both.
        detail += (f"; puslapių riba pasiekta: {', '.join(capped)}"
                   " — kursorius nepajudėjo; taip neturėtų nutikti,"
                   " didinkite SR_POLL_MAX_PAGES")
    blank = [f"{c} ({r['listing_free_page']} psl.)"
             for c, r in per_category.items() if r.get("listing_free_page")]
    if blank:
        # A 200 with no listing links at all. Not an empty category — see
        # _poll_category — so the cursor is held and the run says why.
        detail += (f"; puslapis be skelbimų: {', '.join(blank)}"
                   " — atsakymas be nuorodų; kursorius nepajudėjo")
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
