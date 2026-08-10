"""POST /api/ingest/poll and the ingest.sources block of GET /api/schema.

Task 11 wires the policy-gated poller (Task 10) into the app: a route that
triggers a poll on demand, and a schema block that lets the UI show which
sources are polled and on what robots.txt authority.

These tests use TestClient over a bare app that only includes the router —
the same pattern test_paste.py already uses — rather than starting uvicorn.
The real lifespan (backend.app.main:app) downloads ~35k nature features on
first boot, which takes several minutes and reaches external services; a
bare router + TestClient avoids that entirely while still exercising real
HTTP request/response handling.

poll_all() is monkeypatched at the api module (where the route looks it up)
so no test ever reaches the network — sources/registry.py would refuse most
fetches anyway, but rinka.lt is genuinely pollable and a live test must not
depend on it being reachable.
"""
import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import api as api_module
from backend.app.db import init_db
from backend.app.sources import poller
from backend.app.sources import registry as reg

init_db()

app = FastAPI()
app.include_router(api_module.router)
client = TestClient(app)


# ----------------------------------------------------------------- schema
def test_schema_exposes_polled_list():
    r = client.get("/api/schema")
    assert r.status_code == 200
    assert r.json()["ingest"]["polled"] == poller.POLLED
    assert "rinka" in r.json()["ingest"]["polled"]


def test_schema_exposes_every_registry_source_with_its_policy_authority():
    body = client.get("/api/schema").json()
    sources = body["ingest"]["sources"]
    assert len(sources) == len(reg.SOURCES)
    by_key = {s["key"]: s for s in sources}
    rinka = by_key["rinka"]
    assert rinka["host"] == "www.rinka.lt"
    assert rinka["policy"] == "poll"
    assert "Disallow" in rinka["robots"]
    assert rinka["checked_at"] == "2026-08-10"
    # An alert-only source must show up too, with its own (non-poll) policy —
    # the whole point of the block is showing what is and is not fetched.
    assert by_key["aruodas"]["policy"] == "alert_only"


# ------------------------------------------------------------- poll route
def _fake_poll_all(result):
    async def fake(fetch=None):
        return result
    return fake


def test_ingest_poll_returns_the_per_source_result(monkeypatch):
    fake_result = {
        "rinka": {"status": "ok", "created": [], "scanned": 3, "rejected": 3},
    }
    monkeypatch.setattr(api_module, "poll_all", _fake_poll_all(fake_result))
    pushed = []

    async def fake_push(created):
        pushed.append(created)

    monkeypatch.setattr(api_module.notify, "push", fake_push)

    r = client.post("/api/ingest/poll")

    assert r.status_code == 200
    assert r.json() == fake_result
    assert pushed == []  # nothing created -> nothing pushed


def test_ingest_poll_pushes_only_created_listings_not_near_misses(monkeypatch):
    """created must reach notify.push; anything else in a source's result
    (near-misses, counts) must not — a near-miss reaching Telegram would
    defeat the whole point of the near-miss tier being a quiet, browse-only
    bucket."""
    fake_result = {
        "rinka": {"status": "ok",
                  "created": [{"ref": "K001", "title": "Sodyba A"}],
                  "near": [{"ref": "K002", "title": "Sodyba B (near miss)"}],
                  "scanned": 5, "rejected": 3},
        "zudc": {"status": "ok", "created": [{"ref": "K003", "title": "Sodyba C"}]},
    }
    monkeypatch.setattr(api_module, "poll_all", _fake_poll_all(fake_result))

    pushed = []

    async def fake_push(created):
        pushed.append(created)

    monkeypatch.setattr(api_module.notify, "push", fake_push)

    r = client.post("/api/ingest/poll")

    assert r.status_code == 200
    assert r.json() == fake_result
    assert len(pushed) == 1
    refs = {c["ref"] for c in pushed[0]}
    assert refs == {"K001", "K003"}
    assert "K002" not in refs  # the near-miss must never reach notify.push


def test_ingest_poll_does_not_reach_the_network(monkeypatch):
    """Guard against accidentally wiring the real poll_all into this test
    module: if the route ever bypassed the monkeypatch, this would hang or
    fail on DNS/network instead of returning instantly."""
    fake_result = {"rinka": {"status": "ok", "created": []}}
    monkeypatch.setattr(api_module, "poll_all", _fake_poll_all(fake_result))
    r = client.post("/api/ingest/poll")
    assert r.status_code == 200


# ----------------------------------------- poll_all's own PolicyError handling
def test_poll_all_turns_a_policy_error_into_a_per_source_error_status(monkeypatch):
    """poll_source raises PolicyError for a non-POLL source (see
    test_registry.py), but poll_all wraps every source's call in a
    try/except and stores the failure as that key's result instead of
    letting the exception propagate. This is what keeps
    POST /api/ingest/poll from ever leaking a 500 if the registry and the
    POLLED list ever drift out of sync."""
    monkeypatch.setattr(poller, "POLLED", ["aruodas"])
    result = asyncio.run(poller.poll_all())
    assert result["aruodas"]["status"] == "error"
    assert "aruodas" in result["aruodas"]["error"] or "alert_only" in result["aruodas"]["error"]
