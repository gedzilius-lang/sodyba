"""Telling "the advert we asked for" from "whatever the site served instead".

Every page in this file is a REAL rinka.lt response, saved 2026-08-13:

  rinka_detail_live_nominal_price.html        id 4992805, category sodybos
  rinka_detail_live_nominal_price_namai.html  id 4924114, category namai
  rinka_detail_not_found.html                 the body served for a missing id

The first two are the listings that stalled the live poller. 4992805 is an
abandoned homestead in Rokiškio rajono, 33 ares, listed 2023-01-15 — the
operator's thesis exactly — and it names no price and no floor area, so the
old guard (`price_eur is None and house_m2 is None` ⇒ not a listing) called it
a parse failure and froze the sodybos cursor at it. Nothing newer than it had
ever been ingested.

The third page is why the guard exists at all, and it is what makes the field
counting approach unsalvageable rather than merely too strict: see
test_the_old_rule_was_backwards_not_merely_strict.
"""
import asyncio
import pathlib

import pytest

from backend.app.sources import poller
from backend.app.sources.adapters import rinka

FIX = pathlib.Path(__file__).parent / "fixtures"
SODYBA = (FIX / "rinka_detail_live_nominal_price.html").read_text(encoding="utf-8")
NAMAS = (FIX / "rinka_detail_live_nominal_price_namai.html").read_text(encoding="utf-8")
NOT_FOUND = (FIX / "rinka_detail_not_found.html").read_text(encoding="utf-8")
LIVE = (FIX / "rinka_detail_live.html").read_text(encoding="utf-8")
CATEGORY_PAGE = (FIX / "rinka_category_live.html").read_text(encoding="utf-8")

SODYBA_ID = 4992805
NAMAS_ID = 4924114
LIVE_ID = 4936280
SODYBA_URL = f"https://www.rinka.lt/skelbimas/parduodu-apleista-sodyba-id-{SODYBA_ID}"
NAMAS_URL = f"https://www.rinka.lt/skelbimas/ieskome-pirkti-id-{NAMAS_ID}"


# --------------------------------------------------------------- the adapter
def test_the_stalling_page_is_a_real_listing():
    """Read it and see. This is a property, not an error page."""
    d = rinka.parse_detail(SODYBA, SODYBA_URL)
    assert d["title"] == "Parduodu apleistą sodybą"
    assert d["municipality"] == "Rokiškio rajono"
    assert d["locality"] == "Veduviškis"
    assert d["plot_ares"] == 33.0
    assert d["listed_at"] == "2023-01-15"
    assert d["cadastral_no"] == "7355-0003-46"


def test_an_advert_page_declares_which_advert_it_is():
    assert rinka.declared_listing_id(SODYBA) == SODYBA_ID
    assert rinka.declared_listing_id(NAMAS) == NAMAS_ID
    assert rinka.declared_listing_id(LIVE) == LIVE_ID, \
        "the landmark is not new — the page saved 2026-08-10 carries it too"


def test_a_page_that_is_not_an_advert_declares_nothing():
    assert rinka.declared_listing_id(NOT_FOUND) is None
    # A category listing served where a detail page was expected is the same
    # class of answer, and must be refused the same way.
    assert rinka.declared_listing_id(CATEGORY_PAGE) is None
    assert rinka.declared_listing_id("") is None


def test_the_old_rule_was_backwards_not_merely_strict():
    """The reason no rule over the parsed payload can do this job.

    rinka's 404 body renders the site-wide newest-adverts block, so parsing it
    yields a confident price for a property that is not on the page at all —
    215,000 EUR, a Vilnius flat. That is more than the genuine listing at
    4992805 offers, which names no price and no floor area. Any test that
    counts populated fields therefore ranks the error page above the
    homestead, and the old one did exactly that: it admitted the error page
    and refused the listing.
    """
    error_page = rinka.parse_detail(NOT_FOUND, SODYBA_URL)
    listing = rinka.parse_detail(SODYBA, SODYBA_URL)

    def old_rule_says_listing(d):
        return not (d.get("price_eur") is None and d.get("house_m2") is None)

    assert old_rule_says_listing(error_page), "the 404 body used to pass"
    assert not old_rule_says_listing(listing), "the homestead used to be refused"
    assert error_page["price_eur"] == 215000.0

    # Worse still on a redirect that lands somewhere with no listing id in the
    # URL: _content then has no id to bound the slice with, and the same body
    # reads as a fully specified property.
    unbounded = rinka.parse_detail(NOT_FOUND, "https://www.rinka.lt/nera")
    assert (unbounded["price_eur"], unbounded["house_m2"], unbounded["plot_ares"],
            unbounded["municipality"]) == (165000.0, 104.42, 25.13, "Vilkaviškio rajono")

    # The identity test gets both the right way round.
    assert not poller._is_the_listing(error_page, SODYBA_ID)
    assert poller._is_the_listing(listing, SODYBA_ID)


def test_a_different_advert_is_not_this_advert():
    """The redirect the guard was written for, and never actually caught.

    A page that is a perfectly good advert — just not the one we asked for —
    would sail through any field-presence rule and be stored under the
    requested listing's URL.
    """
    other = rinka.parse_detail(NAMAS, NAMAS_URL)
    assert not poller._is_the_listing(other, SODYBA_ID)
    assert poller._is_the_listing(other, NAMAS_ID)


def test_an_adapter_that_reports_no_identity_is_refused_not_assumed():
    assert not poller._is_the_listing({}, SODYBA_ID)
    assert not poller._is_the_listing({"listing_id": None}, SODYBA_ID)
    # A string id is still an id: adapters are not all going to return int.
    assert poller._is_the_listing({"listing_id": str(SODYBA_ID)}, SODYBA_ID)
    assert not poller._is_the_listing({"listing_id": "keturi"}, SODYBA_ID)


# ----------------------------------------------------------------- the price
def test_the_parser_sees_the_nominal_price_and_refuses_it_deliberately():
    """"Kaina: 1,00 €" is a placeholder, and unknown is the honest reading.

    The refusal has to be a decision, not an accident. parsers.PRICE_RE cannot
    see a price below 100 EUR at all (its digit run is \\d{3,8}, a floor
    inherited from a pattern for thousands-separated prices in prose), so
    before this the right answer came out for the wrong reason — and widening
    that regex, an entirely reasonable-looking change, would have started
    storing price_eur = 1.0 on every such advert. That number would flow into
    costs_json["purchase"] and rank the placeholder top of a list ordered by
    EUR per score point.
    """
    assert rinka._price_value("Kaina: 1,00 €") == 1.0, \
        "the parser must SEE the price it is about to refuse"
    assert rinka.parse_detail(SODYBA, SODYBA_URL)["price_eur"] is None
    assert rinka.parse_detail(NAMAS, NAMAS_URL)["price_eur"] is None


def test_the_refused_price_is_still_on_the_row_for_the_operator_to_read():
    """Refused, not disappeared. `raw` becomes the candidate's `notes`."""
    assert "Kaina: 1,00 €" in rinka.parse_detail(SODYBA, SODYBA_URL)["raw"]


def test_a_real_price_is_still_read_from_the_page():
    assert rinka.parse_detail(LIVE, f"https://www.rinka.lt/x-id-{LIVE_ID}")[
        "price_eur"] == 6000.0
    assert rinka._price_value("Kaina: 60 000,00 €") == 60000.0
    assert rinka._price_value("17.500 EUR") == 17500.0
    assert rinka._price_value("Kaina: sutartinė") is None


# ---------------------------------------------------------------- the poller
_ANY_PROFILE = {
    "key": "any", "name": "Bet kas", "note": "", "enabled": True,
    "min_price": 0, "max_price": 10 ** 9, "min_plot_ares": 0,
    "min_house_m2": 0, "municipalities": [], "require_any": [],
    "require_all": [], "exclude_any": [], "sources": [], "centres": [],
    "radius_km": None, "max_lake_m": None, "max_river_m": None,
    "min_lake_ha": None,
}


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


def _links(ids):
    return "".join(
        f'<a href="https://www.rinka.lt/skelbimas/x-id-{i}">x</a>' for i in ids)


def _fetcher(detail_for):
    """sodybos offers SODYBA_ID, namai offers NAMAS_ID; details from `detail_for`."""
    calls = []

    async def fetch(url):
        calls.append(url)
        if "/skelbimas/" in url:
            return detail_for(int(url.rsplit("-id-", 1)[1]))
        offered = SODYBA_ID if "parduodamos-sodybos" in url else NAMAS_ID
        return (200, _links([offered]) if "page=1" in url
                else _links([offered]))       # page 2 adds nothing new: the end
    return fetch, calls


def test_the_listing_that_stalled_the_cursor_now_ingests(monkeypatch):
    """The production incident, end to end, on the real pages."""
    poller._save_cursor("rinka", "sodybos", 4_991_510)   # the live watermark
    poller._save_cursor("rinka", "namai", 4_924_113)
    monkeypatch.setattr(poller, "_profiles", lambda: [_ANY_PROFILE])
    fetch, _ = _fetcher(lambda i: (200, SODYBA if i == SODYBA_ID else NAMAS))

    out = asyncio.run(poller.poll_source("rinka", fetch=fetch, limit=10))

    assert out["categories"]["sodybos"]["stalled_at"] is None
    assert out["categories"]["namai"]["stalled_at"] is None
    assert poller._cursor("rinka", "sodybos") == SODYBA_ID, \
        "the sodybos cursor did not move past the listing it was stuck on"
    assert poller._cursor("rinka", "namai") == NAMAS_ID

    from backend.app.db import connect
    with connect() as cx:
        row = cx.execute(
            "SELECT title, price_eur, plot_ares, municipality, notes FROM candidate "
            "WHERE url LIKE ?", (f"%-id-{SODYBA_ID}",)).fetchone()
    assert row is not None, "the homestead was still not stored"
    assert row["price_eur"] is None, "a placeholder must not be stored as a price"
    assert row["plot_ares"] == 33.0
    assert row["municipality"] == "Rokiškio rajono"
    assert "Kaina: 1,00 €" in row["notes"], \
        "the operator must be able to see what the page said about the price"


def test_a_page_that_is_not_the_advert_still_stalls_the_cursor(monkeypatch):
    """The guard's original job, now actually done.

    rinka answers a missing id with 404 today, so this body arrives behind a
    200 only if the site changes its mind — which is exactly the silent
    redirect that put a non-listing into the pipeline once already.
    """
    poller._save_cursor("rinka", "sodybos", 4_991_510)
    poller._save_cursor("rinka", "namai", NAMAS_ID)
    monkeypatch.setattr(poller, "_profiles", lambda: [_ANY_PROFILE])
    fetch, _ = _fetcher(lambda i: (200, NOT_FOUND))

    out = asyncio.run(poller.poll_source("rinka", fetch=fetch, limit=10))

    assert out["categories"]["sodybos"]["stalled_at"] == SODYBA_ID
    assert poller._cursor("rinka", "sodybos") == 4_991_510, \
        "the cursor passed a listing that was never ingested"
    from backend.app.db import connect
    with connect() as cx:
        assert cx.execute("SELECT COUNT(*) n FROM candidate").fetchone()["n"] == 0, \
            "the error page's 215000 EUR flat entered the candidate pool"


def test_a_redirect_to_a_different_advert_is_refused(monkeypatch):
    """200, a real advert, the wrong one. Storing it would file another
    property's price, place and phone number under this listing's URL."""
    poller._save_cursor("rinka", "sodybos", 4_991_510)
    poller._save_cursor("rinka", "namai", NAMAS_ID)
    monkeypatch.setattr(poller, "_profiles", lambda: [_ANY_PROFILE])
    fetch, _ = _fetcher(lambda i: (200, NAMAS))      # every request answers 4924114

    out = asyncio.run(poller.poll_source("rinka", fetch=fetch, limit=10))

    assert out["categories"]["sodybos"]["stalled_at"] == SODYBA_ID
    assert poller._cursor("rinka", "sodybos") == 4_991_510
    from backend.app.db import connect
    with connect() as cx:
        assert cx.execute(
            "SELECT COUNT(*) n FROM candidate WHERE url LIKE ?",
            (f"%-id-{SODYBA_ID}",)).fetchone()["n"] == 0
