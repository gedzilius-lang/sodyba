import asyncio
import pathlib

import pytest

from backend.app.sources import poller
from backend.app.sources import registry as reg
from backend.app.sources.adapters import rinka

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


# A page past the end of a category is not empty, and not lower-numbered
# either: rinka renders a block of the site's newest listings on every page.
# Measured 2026-08-12 at per_page=200 — sodybos page 1 carried 96 ids, pages
# 2 and 3 the same 10 (5080920 down to 5080627). So the end-of-category
# signal is a page that adds nothing new, and this stub reuses an id the
# fixture already carries. A blank body would instead be a listing-free
# page, which poller._poll_category treats as a stall.
_REPEAT = '<a href="https://www.rinka.lt/skelbimas/x-id-5080474">x</a>'


def _detail_for(url):
    """The saved detail body, declaring itself to be the advert `url` names.

    Every real rinka advert page carries `<meta name="advertisement-id">`, and
    the poller ingests a page only if that id is the one it asked for (see
    poller._is_the_listing). A stub without it is not a stub of a rinka advert
    at all — it is a stub of the 404 body the site serves instead — so the
    fetchers below have to state the identity the real pages state.
    """
    return (f'<meta name="advertisement-id" content="{url.rsplit("-id-", 1)[1]}" />'
            + DETAIL)


def _list_page(url):
    """The saved fixture is page 1 of the category; past it, only the block.

    Serving the fixture for every page number would model a paginator that
    ignores `page` — rinka does not, and a walk that never runs out of new
    ids hits POLL_MAX_PAGES, which is a stall (see poller._poll_category)
    and would hold the cursor back in tests that are not about the cap."""
    return CATEGORY if "page=1&" in url else _REPEAT


def _fake_fetch(calls):
    async def fetch(url):
        calls.append(url)
        return (200, _list_page(url) if "per_page=" in url else _detail_for(url))
    return fetch


def _fake_fetch_with_failure(calls, bad_id_substr, bad_status=503):
    """Like _fake_fetch, but one specific detail page fails."""
    async def fetch(url):
        calls.append(url)
        if "per_page=" in url:
            return (200, _list_page(url))
        if bad_id_substr in url:
            return (bad_status, "")
        return (200, _detail_for(url))
    return fetch


def test_refuses_a_source_that_is_not_pollable():
    with pytest.raises(reg.PolicyError):
        asyncio.run(poller.poll_source("aruodas", fetch=_fake_fetch([])))


def test_refuses_an_unknown_source():
    with pytest.raises(reg.PolicyError):
        asyncio.run(poller.poll_source("nosuchportal", fetch=_fake_fetch([])))


def test_fetches_the_category_page_then_each_listing():
    """Every category the adapter declares is walked, not just the first.

    The fixture is one saved sodybos page, and _fake_fetch serves it for any
    list URL, so each category sees the same three ids — hence three detail
    fetches per category rather than three in total."""
    calls = []
    asyncio.run(poller.poll_source("rinka", fetch=_fake_fetch(calls)))
    assert "per_page=" in calls[0]
    assert (len([c for c in calls if "/skelbimas/" in c])
            == 3 * len(rinka.CATEGORIES))


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

    repeat = f'<a href="https://www.rinka.lt/skelbimas/test-id-{ids[-1]}">x</a>'

    async def base_fetch(url):
        if "per_page=" not in url:
            return (200, _detail_for(url))
        return (200, cat_html if "page=1&" in url else repeat)

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
