"""Multi-category polling: every category advances on its own, and a listing
below another category's watermark is still ingested."""
import asyncio
import pathlib

import pytest

from backend.app.sources import poller
from backend.app.sources import registry as reg


def _listing_page(ids):
    """A category listing page carrying links in rinka's real shape."""
    return "".join(
        f'<a href="https://www.rinka.lt/skelbimas/x-id-{i}">x</a>' for i in ids)


def _past_the_end(seen_id):
    """A page past the end of a category, in rinka's real shape.

    It is NOT empty. Measured against the live site 2026-08-12 at
    per_page=200: sodybos page 1 carried 96 ids, pages 2 and 3 the same 10
    (5080920 down to 5080627) — a block of the site's newest listings that
    renders on every page of every category. So a page past the end repeats
    ids the walk has already seen, and the end-of-category signal is "this
    page added nothing new". A page with no listing links at all means
    something else entirely, and is tested separately below."""
    return _listing_page([seen_id])


# In tests about namai, sodybos plays "a category with nothing new": a single
# page whose only id already sits at the watermark. The walk ends at page 1
# and no detail is fetched -- the same quiet the old `_listing_page([])` gave,
# but in a shape the real site actually produces.
QUIET_ID = 9
QUIET_SODYBOS = {"parduodamos-sodybos": (200, _listing_page([QUIET_ID]))}


DETAIL = (
    '<h1>Sodyba</h1><span class="price">Kaina: 9000,00 &euro;</span>'
    "<div>Sklypas: 50,00 a. Utenos r. Antažilių k.</div>")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Same reason as test_poller.py: a real 2s pause per fetch, and there are
    several list pages plus a detail page per category here, would make this
    file the slowest in the suite for no gain. The durations are recorded, not
    discarded — the test that cares about them takes this fixture by name."""
    slept: list[float] = []

    async def _fast_sleep(seconds):
        slept.append(seconds)
        return None
    monkeypatch.setattr(poller.asyncio, "sleep", _fast_sleep)
    return slept


def _fetcher(pages, detail=DETAIL):
    """pages: {url_substring: (status, body)}. Records call order."""
    calls = []

    async def fetch(url):
        calls.append(url)
        for frag, resp in pages.items():
            if frag in url:
                return resp
        return (200, detail)

    return fetch, calls


def _last_log(source="rinka"):
    """The row POST /api/ingest/log will show for this run: status + detail."""
    from backend.app.db import connect
    with connect() as cx:
        row = cx.execute(
            "SELECT status, detail FROM refresh_log WHERE source=? "
            "ORDER BY id DESC LIMIT 1", (source,)).fetchone()
    return (row["status"], row["detail"]) if row else ("", "")


def test_a_namai_id_below_the_sodybos_watermark_is_still_ingested(monkeypatch):
    # The bug this whole change exists to prevent. sodybos is far ahead;
    # namai must not inherit its watermark.
    poller._save_cursor("rinka", "sodybos", 5_000_000)
    poller._save_cursor("rinka", "namai", 0)
    fetch, calls = _fetcher({
        "parduodamos-sodybos?page=1": (200, _listing_page([5_000_001])),
        "parduodamos-sodybos?page=2": (200, _past_the_end(5_000_001)),
        "parduodami-namai?page=1": (200, _listing_page([4_000_001])),
        "parduodami-namai?page=2": (200, _past_the_end(4_000_001)),
    })
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    out = asyncio.run(poller.poll_source("rinka", fetch=fetch, limit=10))
    assert out["status"] == "ok"
    fetched = [c for c in calls if "/skelbimas/" in c]
    assert any("id-4000001" in c for c in fetched), \
        "the low-numbered namai listing was skipped"
    assert poller._cursor("rinka", "namai") == 4_000_001
    assert poller._cursor("rinka", "sodybos") == 5_000_001


def test_pagination_walks_until_a_page_yields_nothing_new(monkeypatch):
    poller._save_cursor("rinka", "sodybos", QUIET_ID)
    poller._save_cursor("rinka", "namai", 0)
    pages = {
        "parduodami-namai?page=1": (200, _listing_page([100, 101])),
        "parduodami-namai?page=2": (200, _listing_page([102])),
        "parduodami-namai?page=3": (200, _past_the_end(102)),
        **QUIET_SODYBOS,
    }
    fetch, calls = _fetcher(pages)
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    asyncio.run(poller.poll_source("rinka", fetch=fetch, limit=50))
    assert any("page=2" in c for c in calls)
    assert any("page=3" in c for c in calls)
    assert not any("page=4" in c for c in calls)


def test_a_first_sweep_walks_past_page_one_before_it_picks_a_batch(monkeypatch):
    """The pagination regression test.

    list_ids returns ids DESCENDING, so page 2 holds strictly lower ids than
    page 1 — which is exactly what an oldest-first batch processes first.
    Stopping the page walk as soon as `limit` ids are in hand (the version
    this test was written against) took the batch off page 1 alone and then
    moved the watermark above everything the unfetched pages held, losing
    those listings on every future run."""
    poller._save_cursor("rinka", "sodybos", QUIET_ID)
    poller._save_cursor("rinka", "namai", 0)
    pages = {
        "parduodami-namai?page=1": (200, _listing_page(range(1000, 1020))),
        "parduodami-namai?page=2": (200, _listing_page(range(980, 1000))),
        "parduodami-namai?page=3": (200, _past_the_end(980)),
        **QUIET_SODYBOS,
    }
    fetch, calls = _fetcher(pages)
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    asyncio.run(poller.poll_source("rinka", fetch=fetch, limit=5))
    assert any("parduodami-namai?page=2" in c for c in calls), \
        "page 2 was never fetched, and every id on it is below page 1's"
    fetched = {int(c.rsplit("-id-", 1)[1]) for c in calls if "/skelbimas/" in c}
    assert fetched == set(range(980, 985)), \
        f"the batch must be the oldest ids in the whole sweep, got {fetched}"
    assert poller._cursor("rinka", "namai") == 984, \
        "the cursor advanced past ids that were never fetched"


def test_max_pages_caps_a_never_ending_category(monkeypatch):
    poller._save_cursor("rinka", "sodybos", QUIET_ID)
    poller._save_cursor("rinka", "namai", 0)
    monkeypatch.setattr(poller, "POLL_MAX_PAGES", 2)
    counter = {"n": 300}

    async def fetch(url):
        if "parduodamos-sodybos" in url:
            return (200, _listing_page([QUIET_ID]))
        if "/skelbimas/" in url:
            return (200, DETAIL)
        counter["n"] += 1                     # every page looks fresh
        return (200, _listing_page([counter["n"]]))

    monkeypatch.setattr(poller, "_profiles", lambda: [])
    out = asyncio.run(poller.poll_source("rinka", fetch=fetch, limit=500))
    assert out["categories"]["namai"]["pages_capped"] is True
    # A capped run is an incomplete run. Design section 6 requires it say so
    # rather than looking complete, and the log line is where an operator
    # would see it.
    assert "puslapių riba pasiekta: namai" in _last_log()[1]


def test_a_capped_walk_leaves_the_cursor_where_it_was(monkeypatch):
    """The pagination bug again, on the path the cap takes.

    Pages run descending, so the pages a capped walk never reached hold ids
    BELOW everything it did fetch. Advancing the watermark over the batch
    buries them exactly as the deleted early stop did. The cap is expressed
    as a stall at the lowest fetched id, so the contiguous-advance rule the
    loop already enforces withholds the cursor — and the ids that were
    fetched are still ingested, because _insert is idempotent."""
    poller._save_cursor("rinka", "sodybos", QUIET_ID)
    poller._save_cursor("rinka", "namai", 0)
    monkeypatch.setattr(poller, "POLL_MAX_PAGES", 2)
    fetch, calls = _fetcher({
        "parduodami-namai?page=1": (200, _listing_page(range(1000, 1020))),
        "parduodami-namai?page=2": (200, _listing_page(range(980, 1000))),
        "parduodami-namai?page=3": (200, _listing_page(range(960, 980))),
        **QUIET_SODYBOS,
    })
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    out = asyncio.run(poller.poll_source("rinka", fetch=fetch, limit=40))

    assert not any("parduodami-namai?page=3" in c for c in calls), \
        "the cap never fired, so this test proves nothing"
    assert poller._cursor("rinka", "namai") == 0, \
        "the cursor moved above ids on page 3, which was never fetched"
    assert out["categories"]["namai"]["stalled_at"] == 980
    # The withheld cursor must not cost the run its work.
    assert out["categories"]["namai"]["scanned"] == 40
    detail = _last_log()[1]
    assert "puslapių riba pasiekta: namai" in detail
    assert "SR_POLL_MAX_PAGES" in detail, \
        "a capped category makes no progress — the operator must be told why"


def test_a_listing_free_page_is_a_stall_not_the_end_of_the_category(monkeypatch):
    """A 200 carrying no listing links at all is not an empty category.

    Measured against the live site 2026-08-12, every real page renders
    listings — a page past the end of a category still carries the block of
    the site's ~10 newest (see _past_the_end). So a link-free 200 is a rate
    limiter, a maintenance notice, or a render this parser does not
    understand, wearing a success code. Reading it as the end of the walk
    would advance the watermark over every lower id on the pages behind it,
    which is the same loss the early stop and the capped walk caused."""
    poller._save_cursor("rinka", "sodybos", QUIET_ID)
    poller._save_cursor("rinka", "namai", 0)
    fetch, calls = _fetcher({
        "parduodami-namai?page=1": (200, _listing_page(range(1000, 1030))),
        "parduodami-namai?page=2": (200, "<h1>Per daug užklausų</h1>"),
        "parduodami-namai?page=3": (200, _listing_page(range(970, 1000))),
        **QUIET_SODYBOS,
    })
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    out = asyncio.run(poller.poll_source("rinka", fetch=fetch, limit=40))

    assert not any("parduodami-namai?page=3" in c for c in calls), \
        "the walk did not stop at the listing-free page, so this proves nothing"
    assert poller._cursor("rinka", "namai") == 0, \
        "the cursor advanced over page 3, which the run never reached"
    assert out["categories"]["namai"]["listing_free_page"] == 2
    assert out["categories"]["namai"]["stalled_at"] == 1000
    # Page 1's listings were fetched, so they are still ingested.
    assert out["categories"]["namai"]["scanned"] == 30
    assert "be skelbimų" in _last_log()[1], \
        "a link-free response must be reported, not swallowed"


def test_a_page_past_the_end_of_a_category_ends_the_walk(monkeypatch):
    """The counterpart: rinka answers an out-of-range page with the newest-
    listings block rather than an empty document (sodybos page 1 carried 96
    ids on 2026-08-12, pages 2 and 3 the same 10). Those ids are the TOP of
    the range, so `if not page_ids` never fires on them — every category
    walked to POLL_MAX_PAGES on every run, and once a capped walk withholds
    its cursor that deadlocked the poller outright. The end-of-category
    signal is a page that adds nothing new."""
    poller._save_cursor("rinka", "sodybos", QUIET_ID)
    poller._save_cursor("rinka", "namai", 0)
    newest = _listing_page([1005, 1004])          # the block, on every page
    fetch, calls = _fetcher({
        "parduodami-namai?page=1": (200, _listing_page(range(1000, 1006))),
        "parduodami-namai": (200, newest),        # pages 2+ past the end
        **QUIET_SODYBOS,
    })
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    out = asyncio.run(poller.poll_source("rinka", fetch=fetch, limit=40))

    assert not any("parduodami-namai?page=3" in c for c in calls), \
        "the walk kept going past the end of the category"
    assert out["categories"]["namai"]["pages_capped"] is False
    assert out["categories"]["namai"]["stalled_at"] is None
    assert poller._cursor("rinka", "namai") == 1005, \
        "a completed walk must advance the cursor"


# Matches anything, so the poll reaches _insert and actually stores a row.
# The real presets would reject these stubs on municipality alone.
_ANY_PROFILE = {
    "key": "any", "name": "Bet kas", "note": "", "enabled": True,
    "min_price": 0, "max_price": 10 ** 9, "min_plot_ares": 0,
    "min_house_m2": 0, "municipalities": [], "require_any": [],
    "require_all": [], "exclude_any": [], "sources": [], "centres": [],
    "radius_km": None, "max_lake_m": None, "max_river_m": None,
    "min_lake_ha": None,
}

_SODYBA_DETAIL = (
    '<h1>Sodyba</h1><span class="price">Kaina: 9000,00 &euro;</span>'
    "<div>Sklypas: 50,00 a. Utenos r. Antažilių k.</div>")
_NAMAS_DETAIL = (
    '<h1>Namas</h1><span class="price">Kaina: 12000,00 &euro;</span>'
    "<div>Sklypas: 30,00 a. Kupiškio r. Skapiškio k.</div>")


def test_a_polled_listing_stores_the_category_it_came_from(monkeypatch):
    """Design 5.4, end to end on the only path that writes the column.

    test_source_category.py builds the listing dict by hand and calls _insert
    directly, so it cannot see whether the poller sets the field at all —
    deleting the assignment in _poll_category left the whole suite green.
    This drives poll_source and reads the stored row.

    The two details differ in municipality and price on purpose: identical
    bodies share a fingerprint, the second insert dedupes into the first, and
    the test would pass without the second category ever storing anything."""
    poller._save_cursor("rinka", "sodybos", 0)
    poller._save_cursor("rinka", "namai", 0)
    monkeypatch.setattr(poller, "_profiles", lambda: [_ANY_PROFILE])

    async def fetch(url):
        if "/skelbimas/" in url:
            return (200, _SODYBA_DETAIL if "id-8100001" in url else _NAMAS_DETAIL)
        if "parduodamos-sodybos" in url:
            return (200, _listing_page([8_100_001]) if "page=1" in url
                    else _past_the_end(8_100_001))
        return (200, _listing_page([8_200_001]) if "page=1" in url
                else _past_the_end(8_200_001))

    asyncio.run(poller.poll_source("rinka", fetch=fetch, limit=10))

    from backend.app.db import connect
    with connect() as cx:
        rows = {r["url"].rsplit("-id-", 1)[1]: r["source_category"]
                for r in cx.execute(
                    "SELECT url, source_category FROM candidate "
                    "WHERE url LIKE '%-id-8_00001'").fetchall()}
    assert rows == {"8100001": "sodybos", "8200001": "namai"}, \
        f"a polled row must carry the category it came from, got {rows}"


def test_a_category_with_nothing_new_is_not_mistaken_for_a_failure(monkeypatch):
    """The steady state, and the reason the listing-free check reads the
    UNFILTERED ids.

    Every run after the first meets a page 1 whose ids all sit at or below
    the watermark. That is a complete, healthy walk: the page is full of
    listings, they are simply ones we already have. Testing the `> since`
    filtered list for emptiness instead conflates it with a link-free
    response and stalls the category on every quiet run — for good, since a
    stalled cursor never advances."""
    poller._save_cursor("rinka", "sodybos", 8_300_005)
    poller._save_cursor("rinka", "namai", 8_300_005)
    fetch, calls = _fetcher({
        "rinka.lt/nekilnojamojo": (200, _listing_page([8_300_005, 8_300_004])),
    })
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    out = asyncio.run(poller.poll_source("rinka", fetch=fetch, limit=40))

    for category in ("sodybos", "namai"):
        r = out["categories"][category]
        assert r["listing_free_page"] is None, \
            "a page full of listings was read as a link-free response"
        assert r["stalled_at"] is None
        assert r["pages_capped"] is False
        assert poller._cursor("rinka", category) == 8_300_005
    assert not any("/skelbimas/" in c for c in calls), "nothing new to fetch"
    assert len(calls) == 2, \
        f"one list page per category and no more, got {calls}"


# ------------------------------------------------- real pages, end to end
# Every other fetcher in this file serves bare <a> links. That is enough to
# exercise the watermark arithmetic and nothing else: such a page carries no
# newest-adverts block and no results furniture, so the pair that actually
# matters -- zero results on a genuine page versus zero results on a page the
# category never rendered -- never reached _poll_category at all. Replacing
# the guard with a constant left the whole suite green. These two drive the
# saved pages through poll_source instead.
_FIX = pathlib.Path(__file__).parent / "fixtures"
LIVE_SODYBOS = (_FIX / "rinka_category_live.html").read_text(encoding="utf-8")
PAST_END = (_FIX / "rinka_category_past_end.html").read_text(encoding="utf-8")
NOT_FOUND = (_FIX / "rinka_category_not_found.html").read_text(encoding="utf-8")


def _real_page_fetcher(page_two):
    """sodybos: a real results page, then `page_two`. namai stays quiet."""
    calls = []

    async def fetch(url):
        calls.append(url)
        if "/skelbimas/" in url:
            return (200, DETAIL)
        if "parduodamos-sodybos" in url:
            return (200, LIVE_SODYBOS if "page=1" in url else page_two)
        return (200, _listing_page([QUIET_ID]))

    return fetch, calls


def test_a_real_past_the_end_page_ends_the_walk_and_advances_the_cursor(monkeypatch):
    """Zero results on a page the category itself rendered is the ordinary
    end of the walk. If this stalls, the poller never advances at all --
    sodybos is 86 listings in one page, so it meets this on every run."""
    poller._save_cursor("rinka", "sodybos", 0)
    poller._save_cursor("rinka", "namai", QUIET_ID)
    fetch, calls = _real_page_fetcher(PAST_END)
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    out = asyncio.run(poller.poll_source("rinka", fetch=fetch, limit=40))

    r = out["categories"]["sodybos"]
    assert r["listing_free_page"] is None, "a real category page read as a failure"
    assert r["stalled_at"] is None
    assert r["scanned"] == 4, "the saved page's four results were not ingested"
    assert poller._cursor("rinka", "sodybos") == 5080474
    assert not any("page=3" in c for c in calls), "the walk did not stop"


def test_a_real_error_page_inside_the_chrome_stalls_rather_than_ending(monkeypatch):
    """The same zero results, from a page the category did not render. The
    newest-adverts block is chrome and is present here too, so nothing about
    the listing markup separates this from the case above -- only the results
    furniture does. Ending the walk here would advance the watermark over
    every id on the pages behind it."""
    poller._save_cursor("rinka", "sodybos", 0)
    poller._save_cursor("rinka", "namai", QUIET_ID)
    fetch, _ = _real_page_fetcher(NOT_FOUND)
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    out = asyncio.run(poller.poll_source("rinka", fetch=fetch, limit=40))

    r = out["categories"]["sodybos"]
    assert r["listing_free_page"] == 2
    assert r["stalled_at"] is not None
    assert poller._cursor("rinka", "sodybos") == 0, \
        "the cursor advanced over pages the run never reached"
    # Page 1's results are still ingested -- only the watermark is withheld.
    assert r["scanned"] == 4
    assert "be skelbimų" in _last_log()[1]


def test_one_category_failing_does_not_stop_the_other(monkeypatch):
    poller._save_cursor("rinka", "sodybos", 0)
    poller._save_cursor("rinka", "namai", 7_000_000)
    fetch, calls = _fetcher({
        "parduodamos-sodybos": (503, ""),
        "parduodami-namai?page=1": (200, _listing_page([7_000_001])),
        "parduodami-namai?page=2": (200, _past_the_end(7_000_001)),
    })
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    out = asyncio.run(poller.poll_source("rinka", fetch=fetch, limit=10))
    assert out["categories"]["sodybos"]["status"] == "error"
    assert out["categories"]["namai"]["status"] == "ok"
    # A failed list page must leave its cursor exactly where it was.
    assert poller._cursor("rinka", "sodybos") == 0
    assert poller._cursor("rinka", "namai") == 7_000_001


def test_the_crawl_delay_is_awaited_before_every_fetch(monkeypatch, _no_sleep):
    """Lawfulness toward a site that permits crawling. Previously verified
    only by hand -- see deferred finding B4. List pages are fetched too now,
    several per run, so the pause has to precede every fetch and not only the
    detail ones -- hence the count, not just the set of durations."""
    poller._save_cursor("rinka", "sodybos", 0)
    poller._save_cursor("rinka", "namai", 0)
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    fetch, calls = _fetcher({
        "parduodamos-sodybos?page=1": (200, _listing_page([10])),
        "parduodamos-sodybos?page=2": (200, _past_the_end(10)),
        "parduodami-namai?page=1": (200, _listing_page([11])),
        "parduodami-namai?page=2": (200, _past_the_end(11)),
    })
    asyncio.run(poller.poll_source("rinka", fetch=fetch, limit=10))
    expected = reg.get("rinka").crawl_delay_s
    assert _no_sleep, "no crawl delay was awaited"
    assert set(_no_sleep) == {expected}, \
        f"every pause must be the declared {expected}s, got {set(_no_sleep)}"
    # Two list pages and one detail page per category, both categories: drop
    # the pause before list fetches and this falls from 6 to 2.
    assert len(_no_sleep) == len(calls) == 6, \
        f"one pause per fetch, got {len(_no_sleep)} for {len(calls)} fetches"


def test_a_stalled_category_is_named_in_the_run_summary(monkeypatch):
    """The stall is what keeps the listing retrievable; if the run does not
    report it, an operator cannot tell a stuck category from a quiet one."""
    poller._save_cursor("rinka", "sodybos", 0)
    poller._save_cursor("rinka", "namai", 0)

    async def fetch(url):
        if "/skelbimas/" in url:
            return (503, "") if "id-501" in url else (200, DETAIL)
        if "parduodamos-sodybos" in url:
            return (200, _listing_page([500, 501, 502] if "page=1" in url
                                       else [502]))
        return (200, _listing_page([700]))

    monkeypatch.setattr(poller, "_profiles", lambda: [])
    out = asyncio.run(poller.poll_source("rinka", fetch=fetch, limit=10))
    assert out["categories"]["sodybos"]["stalled_at"] == 501
    # 502 was ingested after it, but the watermark may not pass 501.
    assert poller._cursor("rinka", "sodybos") == 500
    assert "sodybos ties 501" in _last_log()[1]


def test_an_exception_in_one_category_keeps_the_others_results(monkeypatch):
    """A later category blowing up must not discard an earlier one's work.
    Both callers of poll_source read only `created`, so an aborted pass would
    insert sodybos' matches and advance its cursor with the notification
    never sent — and the cursor makes them unrepeatable. `scanned` rides the
    same aggregation `created` does."""
    poller._save_cursor("rinka", "sodybos", 0)
    poller._save_cursor("rinka", "namai", 0)

    async def fetch(url):
        if "parduodami-namai" in url:
            raise RuntimeError("adapteris netikėtai lūžo")
        if "/skelbimas/" in url:
            return (200, DETAIL)
        if "page=1" in url:
            return (200, _listing_page([600]))
        return (200, _past_the_end(600))

    monkeypatch.setattr(poller, "_profiles", lambda: [])
    out = asyncio.run(poller.poll_source("rinka", fetch=fetch, limit=10))
    assert out["categories"]["sodybos"]["status"] == "ok"
    assert out["categories"]["namai"]["status"] == "error"
    assert out["scanned"] == 1, "sodybos' result was discarded"
    assert poller._cursor("rinka", "sodybos") == 600


def test_a_run_where_no_category_was_reachable_is_not_reported_healthy(monkeypatch):
    """/api/ingest/log shows the status column, so a sweep that fetched
    nothing at all must not read as ok with the failure buried in detail."""
    poller._save_cursor("rinka", "sodybos", 0)
    poller._save_cursor("rinka", "namai", 0)
    fetch, _ = _fetcher({"rinka.lt": (503, "")})
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    out = asyncio.run(poller.poll_source("rinka", fetch=fetch, limit=10))
    assert out["status"] == "error"
    assert _last_log()[0] == "error"
