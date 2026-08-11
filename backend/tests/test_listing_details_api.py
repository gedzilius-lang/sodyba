"""listed_at / days_listed and the contact fields, over the HTTP API.

Follows test_candidates_match_state.py's pattern: a bare FastAPI app with only
the router included (backend.app.main:app downloads ~35k nature features on
first boot, which this suite must never trigger), and rows inserted straight
into the candidate table, since the route is what is under test.

The session database persists between test modules (conftest.py points
SR_DATA_DIR at one temp directory for the whole run), so every ref here uses a
"LD" prefix no other module touches, and the fixture removes them afterwards.
"""
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import api as api_module
from backend.app.db import connect, init_db

init_db()

app = FastAPI()
app.include_router(api_module.router)
client = TestClient(app)


@pytest.fixture(autouse=True)
def _own_rows():
    with connect() as cx:
        cx.execute("DELETE FROM candidate WHERE ref LIKE 'LD%'")
    yield
    with connect() as cx:
        cx.execute("DELETE FROM candidate WHERE ref LIKE 'LD%'")


def _insert(ref: str, **kw) -> int:
    cols = {"source": "rinka", "title": f"Sodyba {ref}", "listed_at": None,
            "contact_phone": None, "contact_email": None, "archived": 0}
    cols.update(kw)
    with connect() as cx:
        cur = cx.execute(
            "INSERT INTO candidate(ref,source,title,listed_at,contact_phone,"
            "contact_email,archived) VALUES(?,?,?,?,?,?,?)",
            (ref, cols["source"], cols["title"], cols["listed_at"],
             cols["contact_phone"], cols["contact_email"], cols["archived"]))
        return cur.lastrowid


def _get(ref: str) -> dict:
    items = client.get("/api/candidates?include_archived=true").json()["items"]
    return next(c for c in items if c["ref"] == ref)


def _stored(cid: int) -> dict:
    with connect() as cx:
        return dict(cx.execute("SELECT * FROM candidate WHERE id=?", (cid,)).fetchone())


# ------------------------------------------------------------- listing date
def test_days_listed_is_derived_from_listed_at():
    posted = date.today() - timedelta(days=42)
    _insert("LD001", listed_at=posted.isoformat())
    c = _get("LD001")
    assert c["listed_at"] == posted.isoformat()
    assert c["days_listed"] == 42


def test_days_listed_counts_the_real_five_year_case():
    """The listing this feature was built for: the saved Prienai homestead has
    been advertised since 2021-07-05 at 6 000 EUR. Five years unsold is the
    strongest evidence available that an asking price is wrong."""
    _insert("LD002", listed_at="2021-07-05")
    c = _get("LD002")
    assert c["days_listed"] == (date.today() - date(2021, 7, 5)).days
    assert c["days_listed"] > 365 * 5


def test_days_listed_is_none_when_the_date_is_unknown():
    _insert("LD003")
    c = _get("LD003")
    assert c["listed_at"] is None
    assert c["days_listed"] is None


def test_an_unparseable_stored_date_yields_none_rather_than_raising():
    _insert("LD004", listed_at="netikra data")
    assert _get("LD004")["days_listed"] is None


def test_a_future_date_does_not_produce_a_negative_day_count():
    ahead = (date.today() + timedelta(days=10)).isoformat()
    _insert("LD005", listed_at=ahead)
    assert _get("LD005")["days_listed"] == 0


# ------------------------------------------------------------------ contacts
def test_contacts_are_returned_when_the_listing_carries_them():
    _insert("LD010", contact_phone="+37067132403", contact_email="a@b.lt")
    c = _get("LD010")
    assert c["contact_phone"] == "+37067132403"
    assert c["contact_email"] == "a@b.lt"


def test_a_listing_without_contacts_reports_none_not_an_empty_string():
    _insert("LD011")
    c = _get("LD011")
    assert c["contact_phone"] is None
    assert c["contact_email"] is None


# ----------------------------------------------------------------- retention
def test_archiving_a_candidate_clears_both_contact_fields():
    """The retention rule, enforced rather than documented.

    These are a private individual's details, held for one purpose: enquiring
    about this property. Archiving is the operator rejecting the property, so
    the purpose is spent and the details go — in the same statement that sets
    the flag, not in a nightly job that might never run.
    """
    cid = _insert("LD020", contact_phone="+37067132403",
                  contact_email="pardavejas@example.lt")
    assert _stored(cid)["contact_phone"] == "+37067132403"

    r = client.patch(f"/api/candidates/{cid}", json={"archived": True})
    assert r.status_code == 200
    assert r.json()["archived"] is True
    assert r.json()["contact_phone"] is None
    assert r.json()["contact_email"] is None

    # Gone from the database, not merely absent from the response.
    row = _stored(cid)
    assert row["archived"] == 1
    assert row["contact_phone"] is None
    assert row["contact_email"] is None


def test_unarchiving_does_not_bring_the_contacts_back():
    cid = _insert("LD021", contact_phone="+37067132403", contact_email="a@b.lt")
    client.patch(f"/api/candidates/{cid}", json={"archived": True})
    r = client.patch(f"/api/candidates/{cid}", json={"archived": False})
    assert r.json()["archived"] is False
    assert r.json()["contact_phone"] is None
    assert r.json()["contact_email"] is None


def test_an_ordinary_edit_of_a_live_candidate_keeps_its_contacts():
    # Only archiving erases them. A note or a price edit must not.
    cid = _insert("LD022", contact_phone="+37067132403", contact_email="a@b.lt")
    r = client.patch(f"/api/candidates/{cid}", json={"notes": "paskambinti"})
    assert r.status_code == 200
    assert r.json()["contact_phone"] == "+37067132403"
    assert r.json()["contact_email"] == "a@b.lt"


def test_archiving_leaves_the_rest_of_the_row_alone():
    # The erasure is targeted: rejecting a property must not damage the record
    # of why it was rejected.
    cid = _insert("LD023", contact_phone="+37067132403", listed_at="2021-07-05")
    client.patch(f"/api/candidates/{cid}",
                 json={"archived": True, "notes": "per toli"})
    row = _stored(cid)
    assert row["listed_at"] == "2021-07-05"
    assert row["title"] == "Sodyba LD023"
    assert row["notes"] == "per toli"
