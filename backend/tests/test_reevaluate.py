"""POST /api/candidates/reevaluate — re-run the current profiles over stored rows.

Profiles are otherwise evaluated exactly once, at ingest. mailbox._insert and
poller.poll_source compute match_state/profiles_json/misses_json and write them;
nothing ever recomputes them afterwards, and re-polling does not help because
_insert short-circuits on `fingerprint` before evaluation runs for a listing
already stored. This endpoint is the only path that goes back and re-scores what
is already in the table against the profiles as they stand today.

Follows test_candidates_match_state.py's pattern: a bare FastAPI app with only
the router included (backend.app.main:app downloads ~35k nature features on
first boot, which this suite must never trigger), and rows inserted directly
into the candidate table rather than through mailbox._insert, since the HTTP
route is what is under test.

The session database persists between test modules (conftest.py points
SR_DATA_DIR at one temp directory for the whole run), so every ref here uses
an "REV" prefix that no other test module touches, and the filter_profiles
setting is saved and restored around each test so this module cannot leak a
custom profile list into tests that run after it.
"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import api as api_module
from backend.app.db import connect, get_setting, init_db, set_setting
from backend.app.filters import PRESETS

init_db()

app = FastAPI()
app.include_router(api_module.router)
client = TestClient(app)

PROFILES_KEY = "filter_profiles"


def _profile(key: str, **overrides) -> dict:
    p = {
        "key": key, "name": key, "note": "", "enabled": True,
        "min_price": None, "max_price": None,
        "min_plot_ares": None, "min_house_m2": None,
        "municipalities": [], "require_any": [], "require_all": [],
        "exclude_any": [], "sources": [], "centres": [], "radius_km": None,
        "max_lake_m": None, "max_river_m": None, "min_lake_ha": None,
    }
    p.update(overrides)
    return p


@pytest.fixture(autouse=True)
def _isolated():
    """Give this module its own profiles and its own rows, and leave no trace."""
    original = get_setting(PROFILES_KEY)
    with connect() as cx:
        cx.execute("DELETE FROM candidate WHERE ref LIKE 'REV%'")
    yield
    # get_setting(...) or PRESETS treats "unset" and "set to PRESETS" the same
    # way for every reader, so restoring PRESETS when nothing was stored before
    # is behaviourally identical to leaving the key absent.
    set_setting(PROFILES_KEY, original if original is not None else PRESETS)
    with connect() as cx:
        cx.execute("DELETE FROM candidate WHERE ref LIKE 'REV%'")


def _insert(ref: str, **kw) -> None:
    cols = {
        "source": "rinka", "title": "Sodyba", "municipality": None, "locality": None,
        "price_eur": None, "house_m2": None, "plot_ares": None,
        "match_state": "match", "profiles_json": "[]", "misses_json": "{}",
        "scores_json": "{}", "checks_json": "{}", "flags_json": "{}",
        "costs_json": "{}", "notes": None, "archived": 0,
    }
    cols.update(kw)
    with connect() as cx:
        cx.execute(
            "INSERT INTO candidate(ref,source,title,municipality,locality,"
            "price_eur,house_m2,plot_ares,match_state,profiles_json,misses_json,"
            "scores_json,checks_json,flags_json,costs_json,notes,archived) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ref, cols["source"], cols["title"], cols["municipality"],
             cols["locality"], cols["price_eur"], cols["house_m2"], cols["plot_ares"],
             cols["match_state"], cols["profiles_json"], cols["misses_json"],
             cols["scores_json"], cols["checks_json"], cols["flags_json"],
             cols["costs_json"], cols["notes"], cols["archived"]),
        )


def _row(ref: str) -> dict:
    with connect() as cx:
        return dict(cx.execute("SELECT * FROM candidate WHERE ref=?", (ref,)).fetchone())


def test_a_near_row_becomes_match_once_its_municipality_is_on_the_widened_list():
    set_setting(PROFILES_KEY, [_profile("widened", municipalities=["Utenos rajono"])])
    _insert("REV001", municipality="Utenos rajono", match_state="near",
            profiles_json=json.dumps(["widened"]),
            misses_json=json.dumps({"widened": [{"field": "municipality", "kind": "soft",
                                                   "text": "Utenos rajono ne profilio sąraše",
                                                   "delta": None}]}))

    r = client.post("/api/candidates/reevaluate")
    assert r.status_code == 200
    body = r.json()
    assert {"ref": "REV001", "from": "near", "to": "match"} in body["transitions"]

    row = _row("REV001")
    assert row["match_state"] == "match"
    assert json.loads(row["profiles_json"]) == ["widened"]
    # Same shape ingest already writes for a clean match (mailbox.py/poller.py:
    # a matched profile keeps its key with an empty miss list, not no key at
    # all) — the old "municipality ne profilio sąraše" miss is gone either way.
    assert json.loads(row["misses_json"]) == {"widened": []}


def test_a_match_row_becomes_near_once_it_no_longer_qualifies():
    """The reverse direction: a profile edit can also make a stored row worse,
    not only better. Narrowing the municipality list to exclude the stored
    row's municipality is a soft (categorical) miss, so it lands on 'near'."""
    set_setting(PROFILES_KEY, [_profile("narrowed", municipalities=["Utenos rajono"])])
    _insert("REV002", municipality="Kauno rajono", match_state="match",
            profiles_json=json.dumps(["narrowed"]))

    r = client.post("/api/candidates/reevaluate")
    assert r.status_code == 200
    assert {"ref": "REV002", "from": "match", "to": "near"} in r.json()["transitions"]
    assert _row("REV002")["match_state"] == "near"


def test_a_match_row_becomes_reject_once_it_hits_an_excluded_word():
    """Same reverse direction, but a hard miss (exclude_any) drops it all the
    way to reject rather than the softer near tier."""
    set_setting(PROFILES_KEY, [_profile("excludes_garage", exclude_any=["garaž"])])
    _insert("REV003", title="Sodyba su garažu", match_state="match",
            profiles_json=json.dumps(["excludes_garage"]))

    r = client.post("/api/candidates/reevaluate")
    assert r.status_code == 200
    assert {"ref": "REV003", "from": "match", "to": "reject"} in r.json()["transitions"]
    assert _row("REV003")["match_state"] == "reject"


def test_dry_run_reports_the_same_transitions_but_writes_nothing():
    set_setting(PROFILES_KEY, [_profile("widened", municipalities=["Utenos rajono"])])
    _insert("REV004", municipality="Utenos rajono", match_state="near",
            profiles_json=json.dumps(["widened"]))
    before = _row("REV004")

    r = client.post("/api/candidates/reevaluate", json={"dry_run": True})
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert {"ref": "REV004", "from": "near", "to": "match"} in body["transitions"]

    after = _row("REV004")
    assert after["match_state"] == before["match_state"] == "near"
    assert after["profiles_json"] == before["profiles_json"]
    assert after["misses_json"] == before["misses_json"]
    assert after["updated_at"] == before["updated_at"]


def test_hand_set_scores_and_checks_survive_byte_for_byte():
    """The most important test in the file: a score set after visiting a place
    must never be touched by a machine re-run. Compare the raw stored JSON
    strings, not a reparsed/re-serialised equivalent, so reordering keys or
    reformatting numbers would also be caught."""
    set_setting(PROFILES_KEY, [_profile("widened", municipalities=["Utenos rajono"])])
    scores = '{"location": 9, "condition": 3.14159, "custom": -1}'
    checks = '{"ntr_extract": true, "power": false}'
    _insert("REV005", municipality="Utenos rajono", match_state="near",
            profiles_json=json.dumps(["widened"]),
            scores_json=scores, checks_json=checks)

    r = client.post("/api/candidates/reevaluate")
    assert r.status_code == 200
    assert any(t["ref"] == "REV005" for t in r.json()["transitions"])  # sanity: it did move

    row = _row("REV005")
    assert row["scores_json"] == scores
    assert row["checks_json"] == checks


def test_an_archived_row_is_not_touched():
    set_setting(PROFILES_KEY, [_profile("widened", municipalities=["Utenos rajono"])])
    _insert("REV006", municipality="Utenos rajono", match_state="near",
            profiles_json=json.dumps(["widened"]), archived=1)
    before = _row("REV006")

    r = client.post("/api/candidates/reevaluate")
    assert r.status_code == 200
    assert not any(t["ref"] == "REV006" for t in r.json()["transitions"])

    after = _row("REV006")
    assert after["match_state"] == before["match_state"] == "near"
    assert after["updated_at"] == before["updated_at"]


def test_notify_push_is_not_called(monkeypatch):
    """Re-evaluating candidates after a profile edit must not fire Telegram
    messages for rows the user already has in front of them — that is what
    mailbox/poller ingestion is for, not this path."""
    called = []

    async def fake_push(created):
        called.append(created)

    monkeypatch.setattr(api_module.notify, "push", fake_push)

    set_setting(PROFILES_KEY, [_profile("widened", municipalities=["Utenos rajono"])])
    _insert("REV007", municipality="Utenos rajono", match_state="near",
            profiles_json=json.dumps(["widened"]))

    r = client.post("/api/candidates/reevaluate")
    assert r.status_code == 200
    assert called == []
