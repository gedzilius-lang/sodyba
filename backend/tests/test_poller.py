import asyncio
import pathlib

import pytest

from backend.app.sources import poller
from backend.app.sources import registry as reg

FIX = pathlib.Path(__file__).parent / "fixtures"
CATEGORY = (FIX / "rinka_category.html").read_text(encoding="utf-8")
DETAIL = (FIX / "rinka_detail.html").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _reset():
    """Each test starts with no watermark and no stored candidates.

    Without this, whichever poll test runs first advances the cursor and the
    rest see an empty listing set — the suite would pass for the wrong reason.
    """
    from backend.app.db import connect
    with connect() as cx:
        cx.execute("DELETE FROM source_cursor")
        cx.execute("DELETE FROM source_category_cursor")
        cx.execute("DELETE FROM candidate")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Sleeping for real here (2s x 3 listings, repeatedly) only slows the
    suite down. The durations are captured rather than discarded: politeness
    toward a site that permits crawling is a real obligation, and nothing
    asserted that the delay actually came from the registry. Tests that want
    the record take this fixture by name."""
    slept: list[float] = []

    async def _fast_sleep(seconds):
        slept.append(seconds)
        return None
    monkeypatch.setattr(poller.asyncio, "sleep", _fast_sleep)
    return slept


def _fake_fetch(calls):
    async def fetch(url):
        calls.append(url)
        return (200, CATEGORY if "per_page=" in url else DETAIL)
    return fetch


def _fake_fetch_with_failure(calls, bad_id_substr, bad_status=503):
    """Like _fake_fetch, but one specific detail page fails."""
    async def fetch(url):
        calls.append(url)
        if "per_page=" in url:
            return (200, CATEGORY)
        if bad_id_substr in url:
            return (bad_status, "")
        return (200, DETAIL)
    return fetch


def test_refuses_a_source_that_is_not_pollable():
    with pytest.raises(reg.PolicyError):
        asyncio.run(poller.poll_source("aruodas", fetch=_fake_fetch([])))


def test_refuses_an_unknown_source():
    with pytest.raises(reg.PolicyError):
        asyncio.run(poller.poll_source("nosuchportal", fetch=_fake_fetch([])))


def test_fetches_the_category_page_then_each_listing():
    calls = []
    asyncio.run(poller.poll_source("rinka", fetch=_fake_fetch(calls)))
    assert "per_page=" in calls[0]
    assert len([c for c in calls if "/skelbimas/" in c]) == 3


def test_second_run_fetches_nothing_new():
    calls = []
    asyncio.run(poller.poll_source("rinka", fetch=_fake_fetch(calls)))
    second = []
    asyncio.run(poller.poll_source("rinka", fetch=_fake_fetch(second)))
    assert [c for c in second if "/skelbimas/" in c] == []


def test_watermark_is_persisted():
    asyncio.run(poller.poll_source("rinka", fetch=_fake_fetch([])))
    from backend.app.db import connect
    with connect() as cx:
        row = cx.execute(
            "SELECT last_id FROM source_category_cursor "
            "WHERE source='rinka' AND category='sodybos'").fetchone()
    assert int(row["last_id"]) == 5080474


def test_a_failed_detail_page_is_retried_on_the_next_run():
    """A transient failure must not let a higher-id sibling carry the
    cursor past it — that loses the listing permanently."""
    first = []
    asyncio.run(poller.poll_source(
        "rinka", fetch=_fake_fetch_with_failure(first, "5078893")))

    second = []
    asyncio.run(poller.poll_source("rinka", fetch=_fake_fetch(second)))

    assert any("5078893" in c for c in second if "/skelbimas/" in c)


def test_the_cursor_does_not_advance_past_a_failure():
    calls = []
    asyncio.run(poller.poll_source(
        "rinka", fetch=_fake_fetch_with_failure(calls, "5078893")))

    from backend.app.db import connect
    with connect() as cx:
        row = cx.execute(
            "SELECT last_id FROM source_category_cursor "
            "WHERE source='rinka' AND category='sodybos'").fetchone()
    assert int(row["last_id"]) < 5078893


def test_a_batch_larger_than_the_limit_is_caught_up_not_skipped():
    ids = [100, 101, 102, 103]
    cat_html = "".join(
        f'<a href="https://www.rinka.lt/skelbimas/test-id-{i}">x</a>' for i in ids)

    async def base_fetch(url):
        return (200, cat_html if "per_page=" in url else DETAIL)

    calls1: list[str] = []
    calls2: list[str] = []

    async def fetch1(url):
        calls1.append(url)
        return await base_fetch(url)

    async def fetch2(url):
        calls2.append(url)
        return await base_fetch(url)

    asyncio.run(poller.poll_source("rinka", fetch=fetch1, limit=2))
    asyncio.run(poller.poll_source("rinka", fetch=fetch2, limit=2))

    seen_ids = {int(u.rsplit("-id-", 1)[1])
               for u in calls1 + calls2 if "/skelbimas/" in u}
    assert seen_ids == set(ids)


def test_the_pause_between_fetches_is_the_registry_crawl_delay(_no_sleep):
    """The registry records what each site's robots.txt permits. If the
    poller pauses for anything other than that declared delay, the policy
    table is decoration — so assert the value, not merely that it slept."""
    asyncio.run(poller.poll_source("rinka", fetch=_fake_fetch([])))
    expected = reg.get("rinka").crawl_delay_s
    assert _no_sleep, "the poller must pause between detail fetches"
    assert set(_no_sleep) == {expected}, \
        f"every pause must be the declared {expected}s, got {_no_sleep}"
