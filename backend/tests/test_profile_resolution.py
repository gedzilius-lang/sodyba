"""Stored profiles override the code presets per key — they do not replace them.

`api.profiles()` was `get_setting(PROFILES_KEY) or PRESETS`: the moment anything
was saved, the stored list became the entire truth. Editing a preset in
filters.py then had no effect on a machine that had ever pressed save, and a
preset ADDED to filters.py never appeared there at all. That is not theoretical
— the widened municipality lists of `09f980c` were dead on the operator's
machine, and `zemaitija_lakes` would have been invisible the same way.

The session database persists between test modules (conftest.py points
SR_DATA_DIR at one temp directory for the whole run), so the filter_profiles
setting is saved and restored around every test here.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import api as api_module
from backend.app.db import get_setting, init_db, set_setting
from backend.app.filters import PRESETS, PRESET_KEYS, resolve_profiles
from backend.app.sources import mailbox, poller

init_db()

app = FastAPI()
app.include_router(api_module.router)
client = TestClient(app)

PROFILES_KEY = "filter_profiles"

# A preset that exists in the code. Named through PRESETS rather than spelled
# out so this file keeps testing the real merge after the preset list changes.
NEW_PRESET = PRESETS[-1]
OLD_PRESET = PRESETS[0]

OPERATOR_ONLY = {
    "key": "rinka_sodybos", "name": "Rinka sodybos", "note": "operator's own",
    "enabled": True, "min_price": 1000, "max_price": 30000,
    "min_plot_ares": 0, "min_house_m2": 0, "municipalities": [],
    "require_any": [], "require_all": [], "exclude_any": [], "sources": ["rinka"],
    "centres": [], "radius_km": None,
    "max_lake_m": None, "max_river_m": None, "min_lake_ha": None,
}


@pytest.fixture(autouse=True)
def _restore_setting():
    original = get_setting(PROFILES_KEY)
    yield
    set_setting(PROFILES_KEY, original if original is not None else PRESETS)


def _keys(profs) -> list[str]:
    return [p["key"] for p in profs]


# ------------------------------------------------------------- the pure merge
def test_absent_setting_yields_exactly_the_presets():
    assert resolve_profiles(None) == PRESETS


def test_empty_stored_list_yields_exactly_the_presets():
    assert resolve_profiles([]) == PRESETS


def test_a_preset_missing_from_the_stored_list_is_still_served():
    """The bug. A list saved before `zemaitija_lakes` existed must not hide it."""
    stored = [p for p in PRESETS if p["key"] != NEW_PRESET["key"]]
    resolved = resolve_profiles(stored)

    assert NEW_PRESET["key"] in _keys(resolved)
    assert next(p for p in resolved if p["key"] == NEW_PRESET["key"]) == NEW_PRESET


def test_a_stored_entry_wins_over_the_code_preset_of_the_same_key():
    """And the operator's hand-edit survives a release that changes that preset."""
    edited = {**OLD_PRESET, "max_price": 99000, "municipalities": ["Utenos rajono"]}
    resolved = resolve_profiles([edited])

    served = next(p for p in resolved if p["key"] == OLD_PRESET["key"])
    assert served["max_price"] == 99000
    assert served["municipalities"] == ["Utenos rajono"]
    # ...and it did not cost us the rest of the presets.
    assert _keys(resolved) == list(PRESET_KEYS)


def test_an_operator_authored_key_survives():
    resolved = resolve_profiles([OPERATOR_ONLY])
    assert _keys(resolved) == list(PRESET_KEYS) + ["rinka_sodybos"]
    assert resolved[-1] == OPERATOR_ONLY


def test_order_is_preset_order_first_then_operator_keys():
    """Independent of the stored list's own ordering, on purpose."""
    scrambled = [OPERATOR_ONLY, PRESETS[2], PRESETS[0]]
    assert _keys(resolve_profiles(scrambled)) == list(PRESET_KEYS) + ["rinka_sodybos"]


def test_a_disabled_preset_stays_disabled():
    """Turning a preset off is the supported way to be rid of it, so it has to
    outlive the merge that reinstates deleted keys."""
    resolved = resolve_profiles([{**OLD_PRESET, "enabled": False}])
    assert next(p for p in resolved if p["key"] == OLD_PRESET["key"])["enabled"] is False


@pytest.mark.parametrize("junk", [{}, "nonsense", 7, [None, 3, "x"], [{"name": "no key"}]])
def test_a_malformed_setting_never_takes_the_profile_list_down(junk):
    assert resolve_profiles(junk) == PRESETS


def test_a_duplicate_stored_key_uses_the_first_entry():
    stored = [{**OLD_PRESET, "max_price": 1}, {**OLD_PRESET, "max_price": 2}]
    resolved = resolve_profiles(stored)
    assert next(p for p in resolved if p["key"] == OLD_PRESET["key"])["max_price"] == 1
    assert len(_keys(resolved)) == len(PRESET_KEYS)


# --------------------------------------------------------- every reader agrees
def test_api_poller_and_mailbox_all_see_a_preset_absent_from_the_stored_list():
    """The poller is where this mattered most: it decides what gets stored."""
    set_setting(PROFILES_KEY, [p for p in PRESETS if p["key"] != NEW_PRESET["key"]])

    assert NEW_PRESET["key"] in _keys(api_module.profiles())
    assert NEW_PRESET["key"] in _keys(mailbox._profiles())
    assert NEW_PRESET["key"] in _keys(poller._profiles())


def test_get_profiles_route_serves_the_merge():
    set_setting(PROFILES_KEY, [OPERATOR_ONLY])
    body = client.get("/api/profiles").json()
    assert _keys(body["profiles"]) == list(PRESET_KEYS) + ["rinka_sodybos"]
    assert body["presets"] == PRESETS


# ----------------------------------------------------------------- the reset
def test_reset_restores_the_presets_without_deleting_operator_profiles():
    set_setting(PROFILES_KEY, [{**OLD_PRESET, "max_price": 99000}, OPERATOR_ONLY])

    body = client.post("/api/profiles/reset").json()

    served = next(p for p in body["profiles"] if p["key"] == OLD_PRESET["key"])
    assert served == OLD_PRESET                        # the edit is gone
    assert OPERATOR_ONLY in body["profiles"]           # the operator's own is not


def test_reset_leaves_no_preset_snapshot_behind():
    """A reset that re-wrote PRESETS into the setting would re-freeze the list:
    every preset key would have a stored copy again and the next release's
    changes could never reach this machine."""
    set_setting(PROFILES_KEY, [{**OLD_PRESET, "max_price": 99000}, OPERATOR_ONLY])

    client.post("/api/profiles/reset")

    assert _keys(get_setting(PROFILES_KEY)) == ["rinka_sodybos"]
    assert api_module.profiles() == list(PRESETS) + [OPERATOR_ONLY]
