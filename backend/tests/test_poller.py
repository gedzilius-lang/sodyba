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
        cx.execute("DELETE FROM candidate")


def _fake_fetch(calls):
    async def fetch(url):
        calls.append(url)
        return (200, CATEGORY if "per_page=" in url else DETAIL)
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
            "SELECT last_id FROM source_cursor WHERE source='rinka'").fetchone()
    assert int(row["last_id"]) == 5080474
