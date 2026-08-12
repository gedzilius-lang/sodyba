"""Giving up on a listing that fails for ever — visibly, and only then.

The contiguous-advance rule is what stops a listing being silently skipped:
once an id fails, its category's cursor may not pass it, so it is offered
again next run. The cost is that a listing which fails PERMANENTLY pins its
whole category at that id, and in production both rinka categories were
pinned that way — sodybos at 4992805, namai at 4924114 — refetching the same
head every hour while nothing newer could ever arrive.

The fix is not to weaken the rule. It is to stop retrying, after a stated
number of consecutive failures, and to leave a record of what was abandoned
and why: poll_failure, GET /api/ingest/abandoned, and the run's own log line.
"""
import asyncio

import pytest

from backend.app.sources import poller

GOOD_A, BAD, GOOD_B = 500, 501, 502
QUIET_NAMAI = 7_000_000

DETAIL = (
    '<h1>Sodyba</h1><span class="price">Kaina: 9000,00 &euro;</span>'
    "<div>Sklypas: 50,00 a. Utenos r. Antažilių k.</div>")


def _detail_for(url):
    """A stub that declares itself the advert `url` names — see test_poller."""
    return (f'<meta name="advertisement-id" content="{url.rsplit("-id-", 1)[1]}" />'
            + DETAIL)


def _links(ids):
    return "".join(
        f'<a href="https://www.rinka.lt/skelbimas/x-id-{i}">x</a>' for i in ids)


@pytest.fixture(autouse=True)
def _reset():
    from backend.app.db import connect
    with connect() as cx:
        cx.execute("DELETE FROM source_category_cursor")
        cx.execute("DELETE FROM candidate")
        cx.execute("DELETE FROM poll_failure")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _fast_sleep(seconds):
        return None
    monkeypatch.setattr(poller.asyncio, "sleep", _fast_sleep)


def _fetcher(bad_ids, bad_status=503):
    """sodybos offers GOOD_A/BAD/GOOD_B; `bad_ids` fail. namai stays quiet."""
    calls = []

    async def fetch(url):
        calls.append(url)
        if "/skelbimas/" in url:
            listing_id = int(url.rsplit("-id-", 1)[1])
            if listing_id in bad_ids:
                return (bad_status, "")
            return (200, _detail_for(url))
        if "parduodamos-sodybos" in url:
            return (200, _links([GOOD_A, BAD, GOOD_B]))
        return (200, _links([QUIET_NAMAI]))
    return fetch, calls


def _run(fetch, limit=10):
    return asyncio.run(poller.poll_source("rinka", fetch=fetch, limit=limit))


def _last_log():
    from backend.app.db import connect
    with connect() as cx:
        row = cx.execute("SELECT detail FROM refresh_log WHERE source='rinka' "
                         "ORDER BY id DESC LIMIT 1").fetchone()
    return row["detail"] if row else ""


def _failure_row(listing_id=BAD, category="sodybos"):
    from backend.app.db import connect
    with connect() as cx:
        row = cx.execute(
            "SELECT * FROM poll_failure WHERE source='rinka' AND category=? "
            "AND listing_id=?", (category, listing_id)).fetchone()
    return dict(row) if row else None


def test_the_cursor_waits_while_the_listing_is_still_being_retried(monkeypatch):
    """Below the threshold nothing changes: this is the rule still working."""
    poller._save_cursor("rinka", "namai", QUIET_NAMAI)
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    monkeypatch.setattr(poller, "POLL_GIVE_UP_AFTER", 3)

    for attempt in (1, 2):
        fetch, _ = _fetcher({BAD})
        out = _run(fetch)
        assert out["categories"]["sodybos"]["stalled_at"] == BAD
        assert out["categories"]["sodybos"]["given_up"] == []
        assert poller._cursor("rinka", "sodybos") == GOOD_A, \
            "the cursor passed a listing that was never ingested"
        assert _failure_row()["failures"] == attempt
        assert _failure_row()["given_up_at"] is None


def test_the_cursor_moves_past_a_listing_the_poller_has_given_up_on(monkeypatch):
    poller._save_cursor("rinka", "namai", QUIET_NAMAI)
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    monkeypatch.setattr(poller, "POLL_GIVE_UP_AFTER", 3)

    for _ in range(3):
        fetch, _ = _fetcher({BAD})
        out = _run(fetch)

    assert out["categories"]["sodybos"]["stalled_at"] is None, \
        "an abandoned listing is not a stall — that is the whole point"
    assert [g["listing_id"] for g in out["categories"]["sodybos"]["given_up"]] == [BAD]
    assert poller._cursor("rinka", "sodybos") == GOOD_B, \
        "the category is still pinned behind the listing it gave up on"


def test_what_was_abandoned_is_recorded_with_its_url_and_its_reason(monkeypatch):
    poller._save_cursor("rinka", "namai", QUIET_NAMAI)
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    monkeypatch.setattr(poller, "POLL_GIVE_UP_AFTER", 3)
    for _ in range(3):
        fetch, _ = _fetcher({BAD})
        _run(fetch)

    row = _failure_row()
    assert row["failures"] == 3
    assert row["given_up_at"] is not None
    assert row["reason"] == "HTTP 503"
    assert row["url"] == f"https://www.rinka.lt/skelbimas/x-id-{BAD}", \
        "without the URL the operator cannot go and look at what was dropped"
    assert row["first_at"] and row["last_at"]


def test_the_run_that_gives_up_says_so(monkeypatch):
    """A listing leaving the pipeline uningested must be visible in the log."""
    poller._save_cursor("rinka", "namai", QUIET_NAMAI)
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    monkeypatch.setattr(poller, "POLL_GIVE_UP_AFTER", 3)
    for _ in range(3):
        fetch, _ = _fetcher({BAD})
        _run(fetch)

    detail = _last_log()
    assert "atsisakyta" in detail
    assert f"sodybos {BAD}" in detail
    assert "/api/ingest/abandoned" in detail, \
        "the log line must say where the full record is"


def test_the_abandoned_listing_is_never_fetched_again(monkeypatch):
    poller._save_cursor("rinka", "namai", QUIET_NAMAI)
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    monkeypatch.setattr(poller, "POLL_GIVE_UP_AFTER", 3)
    for _ in range(3):
        fetch, _ = _fetcher({BAD})
        _run(fetch)

    # Rewind the cursor so the id would be offered again but for the ledger.
    poller._save_cursor("rinka", "sodybos", 0)
    fetch, calls = _fetcher({BAD})
    _run(fetch)
    assert not any(f"-id-{BAD}" in c for c in calls), \
        "the poller went back for a listing it had given up on"
    assert any(f"-id-{GOOD_A}" in c for c in calls), "the rest must still be read"


def test_a_listing_that_recovers_starts_from_zero_again(monkeypatch):
    """The count is of CONSECUTIVE failures.

    Otherwise a listing that fails once a month is abandoned after a few
    months of otherwise perfect service — and the poller would be counting
    the site's bad days, not the listing's.
    """
    poller._save_cursor("rinka", "namai", QUIET_NAMAI)
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    monkeypatch.setattr(poller, "POLL_GIVE_UP_AFTER", 3)

    for _ in range(2):
        fetch, _ = _fetcher({BAD})
        _run(fetch)
    assert _failure_row()["failures"] == 2

    fetch, _ = _fetcher(set())              # the site recovers
    _run(fetch)
    assert _failure_row() is None, "a run that read the page must clear the count"

    poller._save_cursor("rinka", "sodybos", 0)
    fetch, _ = _fetcher({BAD})
    out = _run(fetch)
    assert out["categories"]["sodybos"]["given_up"] == []
    assert _failure_row()["failures"] == 1


def test_a_category_wide_outage_does_not_count_against_any_listing(monkeypatch):
    """A list page that fails ends the run before any detail is fetched, so a
    site-wide bad hour cannot walk every id toward being abandoned."""
    poller._save_cursor("rinka", "namai", QUIET_NAMAI)
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    monkeypatch.setattr(poller, "POLL_GIVE_UP_AFTER", 3)

    async def dead(url):
        return (503, "")

    for _ in range(5):
        _run(dead)

    from backend.app.db import connect
    with connect() as cx:
        assert cx.execute("SELECT COUNT(*) n FROM poll_failure").fetchone()["n"] == 0


def test_a_page_that_is_not_the_advert_is_given_up_on_too(monkeypatch):
    """The production shape: 200s that are not the listing, run after run."""
    poller._save_cursor("rinka", "namai", QUIET_NAMAI)
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    monkeypatch.setattr(poller, "POLL_GIVE_UP_AFTER", 3)

    async def fetch(url):
        if "/skelbimas/" in url:
            if f"-id-{BAD}" in url:
                return (200, "<h1>Skelbimas nerastas</h1>")   # 200, no identity
            return (200, _detail_for(url))
        if "parduodamos-sodybos" in url:
            return (200, _links([GOOD_A, BAD, GOOD_B]))
        return (200, _links([QUIET_NAMAI]))

    for _ in range(3):
        out = _run(fetch)

    assert poller._cursor("rinka", "sodybos") == GOOD_B
    assert "neprisistato" in _failure_row()["reason"]


def test_the_abandoned_listing_is_on_the_api(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app.main import app

    poller._save_cursor("rinka", "namai", QUIET_NAMAI)
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    monkeypatch.setattr(poller, "POLL_GIVE_UP_AFTER", 3)
    for _ in range(3):
        fetch, _ = _fetcher({BAD})
        _run(fetch)

    body = TestClient(app).get("/api/ingest/abandoned").json()
    assert body["given_up"] == 1
    row = next(r for r in body["items"] if r["listing_id"] == BAD)
    assert row["category"] == "sodybos"
    assert row["reason"] == "HTTP 503"
    assert row["url"].endswith(f"-id-{BAD}")
