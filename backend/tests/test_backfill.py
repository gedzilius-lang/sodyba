"""sources/backfill.py — refill listing details on rows that predate the columns.

Injected fetcher throughout: nothing here touches the network. The fixtures are
the same saved rinka.lt pages the poller tests use, so the parsing under test is
the shipped `adapters.rinka.parse_detail` and not a copy.

The session database persists between test modules (conftest.py points
SR_DATA_DIR at one temp directory for the whole run), so every row here uses a
"BFL" ref prefix that no other module touches, and the fixture removes them.
"""
import asyncio
import pathlib

import pytest

from backend.app.db import connect, init_db
from backend.app.sources import backfill

init_db()

FIX = pathlib.Path(__file__).parent / "fixtures"
# Carries an infoBlock date (2021-07-05), a phone, and a plot size.
LIVE = (FIX / "rinka_detail_live.html").read_text(encoding="utf-8")
# Same site, no infoBlock — the unreadable-date case.
NO_DATE = (FIX / "rinka_detail.html").read_text(encoding="utf-8")

URL = "https://www.rinka.lt/skelbimas/sodyba-prienuose-id-{}"


@pytest.fixture(autouse=True)
def _rows():
    _purge()
    yield
    _purge()


def _purge() -> None:
    with connect() as cx:
        cx.execute("DELETE FROM candidate WHERE ref LIKE 'BFL%'")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """The crawl delay is real (2 s per rinka.lt page) and must not be waited
    out here. Captured rather than discarded — politeness toward a site that
    permits crawling is an obligation, and one test asserts on the record."""
    slept: list[float] = []

    async def _fast_sleep(seconds):
        slept.append(seconds)
        return None
    monkeypatch.setattr(backfill.asyncio, "sleep", _fast_sleep)
    return slept


def _insert(ref: str, source: str = "rinka", url: str | None = None,
            archived: int = 0, **cols) -> int:
    row = {"listed_at": None, "contact_phone": None, "contact_email": None,
           "house_m2": None, "plot_ares": None, "price_eur": 6000.0,
           "title": ref, "municipality": "Prienų rajono"}
    row.update(cols)
    names = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    with connect() as cx:
        cur = cx.execute(
            f"INSERT INTO candidate(ref, source, url, archived, {names}) "
            f"VALUES(?,?,?,?,{marks})",
            (ref, source, URL.format(ref[-3:]) if url is None else url,
             archived, *row.values()))
        return cur.lastrowid


def _read(ref: str) -> dict:
    with connect() as cx:
        return dict(cx.execute("SELECT * FROM candidate WHERE ref=?", (ref,)).fetchone())


def _fetcher(calls: list[str], page: str = LIVE, status: int = 200):
    async def fetch(url):
        calls.append(url)
        return (status, page)
    return fetch


def _run(**kw) -> dict:
    return asyncio.run(backfill.backfill_details(**kw))


# ------------------------------------------------------------------ the job
def test_it_fills_the_columns_the_poller_can_no_longer_reach():
    _insert("BFL001")
    calls: list[str] = []

    out = _run(fetch=_fetcher(calls))

    assert (out["examined"], out["fetched"], out["updated"], out["failed"]) == (1, 1, 1, 0)
    row = _read("BFL001")
    assert row["listed_at"] == "2021-07-05"
    assert row["contact_phone"] == "+37061234567"
    assert row["plot_ares"] == 118.0
    assert calls == [URL.format("001")]


def test_a_second_run_is_free():
    _insert("BFL002")
    _run(fetch=_fetcher([]))

    calls: list[str] = []
    out = _run(fetch=_fetcher(calls))

    assert calls == []
    assert (out["examined"], out["fetched"], out["updated"]) == (0, 0, 0)
    assert _read("BFL002")["listed_at"] == "2021-07-05"


def test_it_never_overwrites_a_value_already_stored():
    """A backfill fills blanks. A floor area corrected by hand after reading the
    advert, or a contact already captured, is not a blank."""
    _insert("BFL003", plot_ares=42.0, contact_phone="+37060000000")

    _run(fetch=_fetcher([]))

    row = _read("BFL003")
    assert row["plot_ares"] == 42.0
    assert row["contact_phone"] == "+37060000000"
    assert row["listed_at"] == "2021-07-05"        # the blank one did get filled


def test_the_crawl_delay_the_registry_declares_is_waited_out(_no_sleep):
    _insert("BFL004")
    _run(fetch=_fetcher([]))
    assert _no_sleep == [2.0]                      # registry.SOURCES: rinka, 2.0 s


def test_limit_caps_the_pages_fetched_and_reports_the_remainder():
    for ref in ("BFL005", "BFL006", "BFL007"):
        _insert(ref)
    calls: list[str] = []

    out = _run(fetch=_fetcher(calls), limit=2)

    assert len(calls) == 2
    assert (out["fetched"], out["updated"], out["pending"]) == (2, 2, 1)
    assert _read("BFL007")["listed_at"] is None


# --------------------------------------------------------- the archive rule
def test_an_archived_row_is_never_fetched_and_its_erased_contacts_stay_erased():
    """api.update_candidate deletes contact_phone/contact_email when a row is
    archived, on purpose. Refetching the advert would put them straight back."""
    _insert("BFL008", archived=1)
    calls: list[str] = []

    out = _run(fetch=_fetcher(calls))

    assert calls == []
    assert (out["examined"], out["fetched"], out["skipped_archived"]) == (1, 0, 1)
    row = _read("BFL008")
    assert row["contact_phone"] is None and row["contact_email"] is None
    assert row["listed_at"] is None


def test_a_row_archived_mid_run_is_not_written():
    """The app keeps serving while this runs, so the row can be archived
    between the SELECT and the UPDATE. The WHERE clause re-checks."""
    _insert("BFL009")

    async def fetch(url):
        with connect() as cx:
            cx.execute("UPDATE candidate SET archived=1 WHERE ref='BFL009'")
        return (200, LIVE)

    out = _run(fetch=fetch)

    assert (out["fetched"], out["updated"], out["skipped_archived"]) == (1, 0, 1)
    row = _read("BFL009")
    assert row["listed_at"] is None and row["contact_phone"] is None


# ------------------------------------------------------------------ lawfulness
@pytest.mark.parametrize("source, url", [
    ("aruodas", "https://www.aruodas.lt/skelbimas-1"),        # ALERT_ONLY
    ("facebook", "https://www.facebook.com/marketplace/1"),   # MANUAL
    ("zillow", "https://www.zillow.com/1"),                   # not in the registry
])
def test_a_source_the_registry_forbids_is_refused_before_any_fetch(source, url):
    _insert("BFL010", source=source, url=url)
    calls: list[str] = []

    out = _run(fetch=_fetcher(calls))

    assert calls == []
    assert (out["skipped_policy"], out["fetched"], out["updated"]) == (1, 0, 0)


def test_a_url_pointing_off_the_declared_host_is_refused():
    """The source key is POLL, but the stored link is not that host, and the
    registry's verdict is about a host."""
    _insert("BFL011", source="rinka", url="https://www.aruodas.lt/skelbimas-1")
    calls: list[str] = []

    out = _run(fetch=_fetcher(calls))

    assert calls == []
    assert out["skipped_policy"] == 1


# ---------------------------------------------------------------- failures
def test_a_non_200_leaves_the_row_exactly_as_it_was():
    _insert("BFL012")

    out = _run(fetch=_fetcher([], status=404))

    assert (out["fetched"], out["failed"], out["updated"]) == (1, 1, 0)
    row = _read("BFL012")
    assert row["listed_at"] is None and row["contact_phone"] is None


def test_a_page_whose_date_cannot_be_read_writes_nothing_at_all():
    """Not half a row: the page parses a plot size and a floor area, and neither
    is written, because the page we could not read the date on is not a page to
    trust the rest of."""
    _insert("BFL013")

    out = _run(fetch=_fetcher([], page=NO_DATE))

    assert (out["fetched"], out["failed"], out["updated"]) == (1, 1, 0)
    row = _read("BFL013")
    assert row["listed_at"] is None
    assert row["house_m2"] is None and row["plot_ares"] is None


def test_one_failure_does_not_abort_the_run():
    _insert("BFL014")
    _insert("BFL015")

    async def fetch(url):
        if url.endswith("014"):
            raise TimeoutError("connection timed out")
        return (200, LIVE)

    out = _run(fetch=fetch)

    assert (out["examined"], out["failed"], out["updated"]) == (2, 1, 1)
    assert _read("BFL015")["listed_at"] == "2021-07-05"


# ----------------------------------------------------------------- dry run
def _log_rows() -> int:
    with connect() as cx:
        return cx.execute(
            "SELECT COUNT(*) c FROM refresh_log WHERE source='backfill'").fetchone()["c"]


def test_the_dry_run_fetches_nothing_and_writes_nothing():
    _insert("BFL016")
    _insert("BFL017", archived=1)
    calls: list[str] = []
    logged = _log_rows()

    out = _run(fetch=_fetcher(calls), dry_run=True)

    assert calls == []
    assert out["dry_run"] is True
    assert (out["examined"], out["fetched"], out["updated"]) == (2, 0, 0)
    assert [r["ref"] for r in out["rows"] if r["outcome"] == "would_fetch"] == ["BFL016"]
    assert _read("BFL016")["listed_at"] is None
    assert _log_rows() == logged           # not even the run's own log entry


def test_a_row_with_no_url_is_not_work():
    _insert("BFL018", url="")
    out = _run(fetch=_fetcher([]))
    assert out["examined"] == 0
