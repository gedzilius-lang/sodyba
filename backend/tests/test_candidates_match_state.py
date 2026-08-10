"""GET /api/candidates?match_state=... — the "Beveik" (near-miss) tier.

Task 12 puts the already-built near-miss storage (match_state, misses_json)
in front of the user. The one behaviour that must never regress is the
default: a caller that never touches match_state must still see only full
matches, exactly as before this task existed. These tests insert rows
directly into the candidate table (the same pattern test_ingest_state.py
uses) rather than going through mailbox._insert, since the HTTP layer here
is what is under test, not the poller.

Uses a bare FastAPI app with only the router included, the same pattern
test_poll_route.py documents: the real app (backend.app.main:app) downloads
~35k nature features on first boot, which this test suite must never trigger.
"""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import api as api_module
from backend.app.db import connect, init_db

init_db()

app = FastAPI()
app.include_router(api_module.router)
client = TestClient(app)


def _insert(ref: str, match_state: str, misses: dict | None = None) -> None:
    with connect() as cx:
        cx.execute(
            "INSERT INTO candidate(ref,source,title,match_state,misses_json) "
            "VALUES(?,?,?,?,?)",
            (ref, "rinka", f"Testinis {ref}", match_state,
             json.dumps(misses or {})),
        )


def test_default_view_returns_only_full_matches():
    """No match_state param at all — the pre-Task-12 behaviour."""
    _insert("MS001", "match")
    _insert("MS002", "near", {"p1": [{"field": "price", "kind": "soft",
                                       "text": "kaina 21000 > 20000 EUR",
                                       "delta": 1000}]})

    r = client.get("/api/candidates")
    assert r.status_code == 200
    refs = {c["ref"] for c in r.json()["items"]}
    assert "MS001" in refs
    assert "MS002" not in refs


def test_match_state_near_returns_only_near_misses():
    _insert("MS010", "match")
    _insert("MS011", "near", {"p1": [{"field": "price", "kind": "soft",
                                       "text": "kaina 21000 > 20000 EUR",
                                       "delta": 1000}]})

    r = client.get("/api/candidates?match_state=near")
    assert r.status_code == 200
    refs = {c["ref"] for c in r.json()["items"]}
    assert "MS011" in refs
    assert "MS010" not in refs
    # the reason the near-miss almost matched must reach the payload
    near = next(c for c in r.json()["items"] if c["ref"] == "MS011")
    assert near["misses"]["p1"][0]["text"] == "kaina 21000 > 20000 EUR"


def test_match_state_all_returns_both_tiers():
    _insert("MS020", "match")
    _insert("MS021", "near")

    r = client.get("/api/candidates?match_state=all")
    assert r.status_code == 200
    refs = {c["ref"] for c in r.json()["items"]}
    assert "MS020" in refs
    assert "MS021" in refs
