"""Multi-category polling: every category advances on its own, and a listing
below another category's watermark is still ingested."""
import asyncio

import pytest

from backend.app.sources import poller
from backend.app.sources import registry as reg


def _listing_page(ids):
    """A category listing page carrying links in rinka's real shape."""
    return "".join(
        f'<a href="https://www.rinka.lt/skelbimas/x-id-{i}">x</a>' for i in ids)


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
        "parduodamos-sodybos": (200, _listing_page([5_000_001])),
        "parduodami-namai": (200, _listing_page([4_000_001])),
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
    poller._save_cursor("rinka", "sodybos", 0)
    poller._save_cursor("rinka", "namai", 0)
    pages = {
        "parduodami-namai?page=1": (200, _listing_page([100, 101])),
        "parduodami-namai?page=2": (200, _listing_page([102])),
        "parduodami-namai?page=3": (200, _listing_page([])),
        "parduodamos-sodybos": (200, _listing_page([])),
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
    poller._save_cursor("rinka", "sodybos", 0)
    poller._save_cursor("rinka", "namai", 0)
    pages = {
        "parduodami-namai?page=1": (200, _listing_page(range(1000, 1020))),
        "parduodami-namai?page=2": (200, _listing_page(range(980, 1000))),
        "parduodami-namai?page=3": (200, _listing_page([])),
        "parduodamos-sodybos": (200, _listing_page([])),
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
    poller._save_cursor("rinka", "sodybos", 0)
    poller._save_cursor("rinka", "namai", 0)
    monkeypatch.setattr(poller, "POLL_MAX_PAGES", 2)
    counter = {"n": 300}

    async def fetch(url):
        if "parduodamos-sodybos" in url:
            return (200, _listing_page([]))
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


def test_one_category_failing_does_not_stop_the_other(monkeypatch):
    poller._save_cursor("rinka", "sodybos", 0)
    poller._save_cursor("rinka", "namai", 7_000_000)
    fetch, calls = _fetcher({
        "parduodamos-sodybos": (503, ""),
        "parduodami-namai": (200, _listing_page([7_000_001])),
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
        "parduodamos-sodybos?page=2": (200, _listing_page([])),
        "parduodami-namai?page=1": (200, _listing_page([11])),
        "parduodami-namai?page=2": (200, _listing_page([])),
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
        if "parduodamos-sodybos?page=1" in url:
            return (200, _listing_page([500, 501, 502]))
        return (200, _listing_page([]))

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
        return (200, _listing_page([]))

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
