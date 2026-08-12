"""Multi-category polling: every category advances on its own, and a listing
below another category's watermark is still ingested."""
import asyncio

import pytest

from backend.app.sources import poller


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


def test_page_walking_stops_once_the_run_limit_is_reached(monkeypatch):
    poller._save_cursor("rinka", "sodybos", 0)
    poller._save_cursor("rinka", "namai", 0)
    pages = {
        "parduodami-namai?page=1": (200, _listing_page(range(200, 210))),
        "parduodamos-sodybos": (200, _listing_page([])),
    }
    fetch, calls = _fetcher(pages)
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    asyncio.run(poller.poll_source("rinka", fetch=fetch, limit=3))
    # No point fetching page 2 when this run will not process its ids.
    assert not any("parduodami-namai?page=2" in c for c in calls)


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


def test_the_crawl_delay_is_awaited_with_the_registry_value(monkeypatch, _no_sleep):
    # Lawfulness toward a site that permits crawling. Previously verified only
    # by hand -- see deferred finding B4. List pages are fetched too now, so
    # the pause has to precede every fetch, not only the detail ones.
    poller._save_cursor("rinka", "sodybos", 0)
    poller._save_cursor("rinka", "namai", 0)
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    fetch, _ = _fetcher({
        "parduodamos-sodybos": (200, _listing_page([10])),
        "parduodami-namai": (200, _listing_page([11])),
    })
    asyncio.run(poller.poll_source("rinka", fetch=fetch, limit=10))
    assert _no_sleep, "no crawl delay was awaited"
    assert set(_no_sleep) == {2.0}, \
        f"expected rinka's declared 2.0s, got {set(_no_sleep)}"
