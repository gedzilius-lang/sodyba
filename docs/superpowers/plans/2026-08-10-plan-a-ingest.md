# Plan A — Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Sodyba Radar a second, lawful ingest path that polls permitted sources, stop silently discarding listings that only just miss a filter, and collapse the duplicates that three ingest paths will inevitably produce.

**Architecture:** A PURE source registry declares a policy per source and the fetcher physically refuses anything not marked `POLL`. `filters.py` stops short-circuiting and returns every miss classified hard or soft, so soft-only misses become a visible "Beveik" tier instead of being dropped. Site adapters split into PURE parse functions plus an injected async fetcher, so every parser is tested against saved HTML with no network.

**Tech Stack:** Python 3.12, FastAPI, SQLite (WAL), APScheduler, httpx (already present). pytest added as the only new dependency, dev-only. Frontend stays vanilla JS.

## Global Constraints

- **No build step, no npm.** Frontend is hand-written JS/HTML/CSS served statically. From `AGENT.md`.
- **PURE modules do no I/O.** `geo.py`, `scoring.py`, `advisor.py`, `sources/parsers.py`, and every new module marked PURE below. Dependencies are injected.
- **`filters.py` is PURE except `_radius_misses`**, which reaches `sources.nature.geocode` and therefore the database. This is inherited from v1 (`filters.matches` did the same at `filters.py:175`) and is deliberately preserved — profiles must be able to gate on measured distance. Do not "fix" it in this plan, and do not extend the exception to any other function.
- **`robots.txt` is binding.** Never add a fetcher for `evarzytynes.lt`, `aruodas.lt`, `domoplius.lt`, `kampas.lt`, `skelbiu.lt`, `alio.lt`, or `aukcionai.turtas.lt`. If asked, refuse and explain. From `AGENT.md` section 3.
- **All user-facing strings are Lithuanian.** Match the existing tone in `filters.py` and `advisor.py`.
- **Additive migrations only.** New columns go in `db.MIGRATIONS` as `(table, column, ddl)`; new tables go in `db.SCHEMA` guarded by `CREATE TABLE IF NOT EXISTS`.
- **No new runtime dependency.** `pytest` and `pytest`-only helpers go in `backend/requirements-dev.txt`, never `requirements.txt`.
- **Every commit message ends with the trailer:**
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  ```
- **Politeness values are per-source**, read from the registry, never hardcoded at a call site. Global fallback is `config.REQUEST_DELAY`.

---

## File Structure

| File | Responsibility | Status |
|---|---|---|
| `pytest.ini` | test discovery + import path | create |
| `backend/requirements-dev.txt` | pytest only | create |
| `backend/tests/conftest.py` | isolated temp DB before any app import | create |
| `backend/tests/test_scoring.py` | locks existing scoring contract | create |
| `backend/tests/test_parsers.py` | price regex, incl. the `60000,00 €` bug | create |
| `backend/tests/test_registry.py` | policy enforcement | create |
| `backend/tests/test_filters.py` | near-miss evaluator | create |
| `backend/tests/test_dedupe.py` | cross-source duplicate detection | create |
| `backend/tests/test_rinka.py` | adapter parsing against saved HTML | create |
| `backend/tests/fixtures/` | saved HTML fixtures | create |
| `backend/app/sources/registry.py` | source policy. **PURE** | create |
| `backend/app/dedupe.py` | fingerprint + similarity. **PURE** | create |
| `backend/app/sources/adapters/__init__.py` | adapter lookup | create |
| `backend/app/sources/adapters/rinka.py` | rinka.lt parse functions. **PURE** | create |
| `backend/app/sources/poller.py` | async fetch loop, policy-gated | create |
| `backend/app/filters.py` | near-miss evaluator | modify |
| `backend/app/sources/parsers.py` | `PRICE_RE` fix | modify |
| `backend/app/db.py` | `source_cursor` table, 2 column migrations | modify |
| `backend/app/sources/mailbox.py` | persist near-misses, use shared dedupe | modify |
| `backend/app/api.py` | `match_state` filter, poll route, schema | modify |
| `backend/app/main.py` | poll scheduler job | modify |
| `backend/app/config.py` | poll interval, rinka categories | modify |
| `backend/app/sources/__init__.py` | re-export poller | modify |
| `frontend/index.html`, `app.js`, `styles.css` | Beveik tier | modify |

---

### Task 1: Test harness and scoring lock

There is no test framework in the repository. `AGENT.md` lists required reference values that were never encoded. Before changing `filters.py` or `parsers.py`, lock the behaviour that must not move.

**Files:**
- Create: `pytest.ini`, `backend/requirements-dev.txt`, `backend/tests/__init__.py`, `backend/tests/conftest.py`, `backend/tests/test_scoring.py`

**Interfaces:**
- Consumes: `backend.app.scoring.evaluate`, `backend.app.scoring.normalised_weights`, `backend.app.scoring.DEFAULT_SETTINGS`
- Produces: a working `pytest` invocation (`python -m pytest`) and an isolated-DB conftest every later task depends on.

- [ ] **Step 1: Create the test configuration**

`pytest.ini` at the repository root:

```ini
[pytest]
testpaths = backend/tests
pythonpath = .
addopts = -q
```

`backend/requirements-dev.txt`:

```
-r requirements.txt
pytest==8.3.4
```

- [ ] **Step 2: Create the isolated-DB conftest**

`backend/app/config.py` reads `SR_DATA_DIR` at import time and calls `mkdir`. The environment variable must therefore be set before any app module is imported. pytest imports `conftest.py` before test modules, so this is the correct place.

`backend/tests/__init__.py` — empty file.

`backend/tests/conftest.py`:

```python
"""Point every test at a throwaway database before the app is imported.

config.py reads SR_DATA_DIR at import time, so this must run first. pytest
imports conftest before any test module, which is what makes it work.
"""
import os
import pathlib
import tempfile

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="sodyba-test-"))
os.environ["SR_DATA_DIR"] = str(_TMP)

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    from backend.app.db import init_db
    init_db()
```

- [ ] **Step 3: Write the failing scoring tests**

`backend/tests/test_scoring.py`:

```python
"""Locks the v1 scoring contract. These values must not move."""
from backend.app.scoring import DEFAULT_SETTINGS, evaluate, normalised_weights

ALL_CRITERIA = ["forest_water", "isolation", "power", "water", "condition",
                "plot_size", "access", "buildability", "price_vs_value", "geopolitics"]


def _candidate(score=7, **kw):
    base = {
        "flags": {},
        "scores": {k: score for k in ALL_CRITERIA},
        "costs": {"purchase": 10000, "roof": 5000},
    }
    base.update(kw)
    return base


def test_default_weights_sum_to_one():
    assert round(sum(DEFAULT_SETTINGS["weights"].values()), 6) == 1.0


def test_uniform_scores_produce_that_score():
    r = evaluate(_candidate(7), DEFAULT_SETTINGS)
    assert r["weighted_score"] == 7.0


def test_cost_contingency_and_eur_per_point():
    r = evaluate(_candidate(7), DEFAULT_SETTINGS)
    assert r["cost_subtotal"] == 15000
    assert r["cost_contingency"] == 2250
    assert r["total_cost"] == 17250
    assert r["eur_per_point"] == 2464          # 17250 / 7.0, rounded
    assert r["verdict"] == "shortlist"


def test_hard_flag_rejects_outright():
    r = evaluate(_candidate(9, flags={"fractional_ownership": True}),
                 DEFAULT_SETTINGS)
    assert r["verdict"] == "rejected"
    assert r["hard_flags_tripped"] == ["Dalinė nuosavybė"]
    assert r["verdict_reason"].startswith("STOP:")


def test_missing_scores_are_incomplete():
    c = _candidate(7)
    del c["scores"]["power"]
    r = evaluate(c, DEFAULT_SETTINGS)
    assert r["weighted_score"] is None
    assert r["verdict"] == "incomplete"


def test_weights_are_renormalised_when_they_do_not_sum_to_one():
    # power raised from 0.15 to 0.28 -> raw total 1.13
    w = normalised_weights({**DEFAULT_SETTINGS["weights"], "power": 0.28})
    assert round(sum(w.values()), 6) == 1.0
    assert round(w["power"], 5) == round(0.28 / 1.13, 5)


def test_garbage_weights_fall_back_to_defaults():
    w = normalised_weights({"power": -1, "water": "nonsense"})
    assert round(sum(w.values()), 6) == 1.0
    assert round(w["power"], 6) == round(DEFAULT_SETTINGS["weights"]["power"], 6)
```

- [ ] **Step 4: Run the tests**

```
pip install -r backend/requirements-dev.txt
python -m pytest backend/tests/test_scoring.py -v
```

Expected: all 7 PASS. These describe code that already exists — they are a lock, not a spec. If any fails, **stop and report**: the shipped scoring differs from what this plan assumes and every later task's expectations need revisiting.

- [ ] **Step 5: Commit**

```bash
git add pytest.ini backend/requirements-dev.txt backend/tests/
git commit -m "test: add pytest harness and lock v1 scoring contract"
```

---

### Task 2: Fix the price regex

`parsers.PRICE_RE` requires the currency token immediately after the digits. rinka.lt renders `60000,00 &euro;`, which becomes `60000,00 €` after `to_text` unescapes it, and the two-decimal tail defeats the match — verified against a live listing on 2026-08-10. Portal alert emails use the same `12 500,00 €` convention, so this silently loses prices on the email path too.

**Files:**
- Modify: `backend/app/sources/parsers.py:21`
- Create: `backend/tests/test_parsers.py`

**Interfaces:**
- Produces: `PRICE_RE` matching an optional `[.,]dd` decimal tail. `_f()` is unchanged and still returns whole euros.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_parsers.py`:

```python
from backend.app.sources import parsers


def _price(text):
    return parsers._f(parsers.PRICE_RE.search(text))


def test_plain_price():
    assert _price("Kaina 5000 Eur") == 5000.0


def test_space_thousands_separator():
    assert _price("17 000 EUR") == 17000.0


def test_dot_thousands_separator():
    assert _price("17.000 EUR") == 17000.0


def test_decimal_comma_tail():
    # rinka.lt renders exactly this; verified on a live listing 2026-08-10
    assert _price("Kaina: 60000,00 €") == 60000.0


def test_decimal_comma_tail_with_thousands_separator():
    assert _price("12 500,50 €") == 12500.0


def test_non_breaking_space_thousands_separator():
    # built from the code point so no editor can turn it back into a space
    assert _price("17" + chr(0xa0) + "000 EUR") == 17000.0


def test_no_price_returns_none():
    assert _price("Sodyba prie ežero") is None
```

Note the two escape sequences in the regex above: `\u00a0` and `\u20ac` are written
as six literal characters each, not as the symbols they denote. `re` resolves them
itself, which keeps the pattern line pure ASCII and immune to an editor rewriting
the file in the wrong encoding. Writing them as literal characters is how the first
attempt at this task shipped a regex that silently returned `0.0` for a
non-breaking-space price.

- [ ] **Step 2: Run it to see it fail**

```
python -m pytest backend/tests/test_parsers.py -v
```

Expected: `test_decimal_comma_tail` and `test_decimal_comma_tail_with_thousands_separator` FAIL. The first three PASS — that is the point, they are the regression guard.

- [ ] **Step 3: Fix the regex**

In `backend/app/sources/parsers.py`, replace line 21:

```python
PRICE_RE = re.compile(r"(\d{1,3}(?:[ .\u00a0]\d{3})+|\d{3,7})\s*(?:EUR|\u20ac|Eur)", re.I)
```

with:

```python
# The optional decimal tail matters: portals render "60000,00 €", and without
# it the currency token no longer follows the digits and nothing matches.
PRICE_RE = re.compile(
    r"(\d{1,3}(?:[ .\u00a0]\d{3})+|\d{3,8})(?:[.,]\d{1,2})?\s*(?:EUR|\u20ac|Eur)", re.I)
```

The captured group still excludes the decimals, so `_f` returns whole euros and needs no change.

- [ ] **Step 4: Run the tests**

```
python -m pytest backend/tests/test_parsers.py -v
```

Expected: all 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/sources/parsers.py backend/tests/test_parsers.py
git commit -m "fix: match prices written with a decimal tail (60000,00 EUR)"
```

---

### Task 3: Source registry

Move the `robots.txt` policy out of `AGENT.md` prose and into code that the fetcher must obey.

**Files:**
- Create: `backend/app/sources/registry.py`, `backend/tests/test_registry.py`

**Interfaces:**
- Produces:
  - `POLL`, `ALERT_ONLY`, `LINK_ONLY`, `MANUAL` — policy string constants
  - `Source` — frozen dataclass `(key, host, policy, robots, checked_at, crawl_delay_s)`
  - `SOURCES: list[Source]`
  - `get(key: str) -> Source | None`
  - `is_pollable(key: str) -> bool`
  - `assert_pollable(key: str) -> Source` — raises `PolicyError`
  - `stale(today: str, max_age_days: int = 90) -> list[str]`
  - `PolicyError(Exception)`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_registry.py`:

```python
import pytest

from backend.app.sources import registry as reg


def test_rinka_is_pollable():
    assert reg.is_pollable("rinka")
    assert reg.get("rinka").crawl_delay_s >= 1.0


def test_ntaukcionai_honours_declared_crawl_delay():
    assert reg.get("ntaukcionai").crawl_delay_s == 10.0


@pytest.mark.parametrize("key", ["aruodas", "domoplius", "evarzytynes",
                                 "kampas", "skelbiu", "alio"])
def test_alert_only_sources_are_not_pollable(key):
    assert not reg.is_pollable(key)
    with pytest.raises(reg.PolicyError):
        reg.assert_pollable(key)


def test_unknown_source_is_refused():
    with pytest.raises(reg.PolicyError):
        reg.assert_pollable("some-portal-we-never-checked")


def test_assert_pollable_returns_the_source():
    assert reg.assert_pollable("rinka").host == "www.rinka.lt"


def test_stale_flags_old_checks_only():
    assert reg.stale("2026-09-01", max_age_days=90) == []
    assert "rinka" in reg.stale("2027-01-01", max_age_days=90)
```

- [ ] **Step 2: Run it to see it fail**

```
python -m pytest backend/tests/test_registry.py -v
```

Expected: FAIL — `ModuleNotFoundError: backend.app.sources.registry`.

- [ ] **Step 3: Write the implementation**

`backend/app/sources/registry.py`:

```python
"""Which sources may be fetched, and on whose authority. PURE.

Every verdict below was read from the source's own robots.txt on the date in
`checked_at`. This is not documentation — `poller.py` calls `assert_pollable`
before it opens a connection, so a source absent from this table, or present
with any policy but POLL, cannot be fetched at all.

robots.txt changes. alio.lt added its AI-crawler blocks in July 2026, one month
before this table was written. `stale()` exists so an unchecked verdict becomes
a warning instead of an assumption.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date

POLL = "poll"
ALERT_ONLY = "alert_only"
LINK_ONLY = "link_only"
MANUAL = "manual"


class PolicyError(Exception):
    """Raised when something tries to fetch a source it may not fetch."""


@dataclass(frozen=True)
class Source:
    key: str
    host: str
    policy: str
    robots: str
    checked_at: str          # ISO date, YYYY-MM-DD
    crawl_delay_s: float = 1.0


SOURCES: list[Source] = [
    Source("data_gov", "get.data.gov.lt", POLL,
           "Allow: / — official open-data API", "2026-08-10", 1.0),
    Source("rinka", "www.rinka.lt", POLL,
           "User-agent: * / Disallow: (empty) — fully open", "2026-08-10", 2.0),
    Source("zudc", "zudc.lt", POLL,
           "Disallow: (empty) — state land auctions", "2026-08-10", 2.0),
    Source("ntaukcionai", "www.ntaukcionai.lt", POLL,
           "Allow with Crawl-delay: 10", "2026-08-10", 10.0),
    Source("adminbiuras", "www.adminbiuras.lt", POLL,
           "Allow: / — bankruptcy estates", "2026-08-10", 2.0),
    Source("turtas", "turtas.lt", POLL,
           "Disallow: (empty)", "2026-08-10", 2.0),

    Source("aukcionai_turtas", "aukcionai.turtas.lt", LINK_ONLY,
           "no robots.txt, but the bundle ships a reCAPTCHA site key", "2026-08-10"),

    Source("evarzytynes", "www.evarzytynes.lt", ALERT_ONLY,
           "Disallow: /", "2026-08-10"),
    Source("aruodas", "www.aruodas.lt", ALERT_ONLY,
           "bot-challenge page even for /robots.txt", "2026-08-10"),
    Source("domoplius", "www.domoplius.lt", ALERT_ONLY,
           "bot-challenge page even for /robots.txt", "2026-08-10"),
    Source("kampas", "www.kampas.lt", ALERT_ONLY,
           "/robots.txt returns HTTP 403", "2026-08-10"),
    Source("skelbiu", "www.skelbiu.lt", ALERT_ONLY,
           "Allow: / but Disallow: /select/ and search params; "
           "blocks anthropic-ai and Claude-Web by name", "2026-08-10"),
    Source("alio", "www.alio.lt", ALERT_ONLY,
           "Disallow: /public/textSearch/*, /public/category/search*; "
           "GPTBot, ClaudeBot, Amazonbot blocked July 2026", "2026-08-10"),

    Source("facebook", "www.facebook.com", MANUAL,
           "ToS forbids automated collection — paste route only", "2026-08-10"),
    Source("manual", "", MANUAL, "pasted by hand", "2026-08-10"),
]

_BY_KEY = {s.key: s for s in SOURCES}


def get(key: str) -> Source | None:
    return _BY_KEY.get(key)


def is_pollable(key: str) -> bool:
    s = _BY_KEY.get(key)
    return bool(s and s.policy == POLL)


def assert_pollable(key: str) -> Source:
    """Gate every outbound fetch. Refuses unknown sources, not just forbidden ones."""
    s = _BY_KEY.get(key)
    if s is None:
        raise PolicyError(
            f"nežinomas šaltinis „{key}“ — įrašyk jį į registry.SOURCES ir "
            f"pirma patikrink jo robots.txt")
    if s.policy != POLL:
        raise PolicyError(
            f"„{key}“ ({s.host}) pažymėtas kaip {s.policy}: {s.robots}. "
            f"Automatinis skaitymas draudžiamas.")
    return s


def stale(today: str, max_age_days: int = 90) -> list[str]:
    """Keys whose robots.txt verdict is older than max_age_days."""
    now = date.fromisoformat(today)
    out = []
    for s in SOURCES:
        if not s.checked_at:
            continue
        if (now - date.fromisoformat(s.checked_at)).days > max_age_days:
            out.append(s.key)
    return out
```

- [ ] **Step 4: Run the tests**

```
python -m pytest backend/tests/test_registry.py -v
```

Expected: all PASS (11 including parametrised cases).

- [ ] **Step 5: Commit**

```bash
git add backend/app/sources/registry.py backend/tests/test_registry.py
git commit -m "feat: source policy registry enforcing robots.txt verdicts in code"
```

---

### Task 4: Filter evaluator that reports every miss

`filters.matches()` returns on the first failed check, so a listing that misses on price alone is indistinguishable from one that misses on six things. Replace the short-circuit with a full evaluation. This task introduces no `NEAR` state — every soft miss still rejects, so behaviour is unchanged. Task 6 turns the tier on.

**Files:**
- Modify: `backend/app/filters.py`
- Create: `backend/tests/test_filters.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `Miss` — dataclass `(field: str, kind: str, text: str, delta: float | None)`
  - `ProfileMatch` — dataclass `(key: str, state: str, misses: list[Miss])`
  - `HARD = "hard"`, `SOFT = "soft"`, `MATCH = "match"`, `NEAR = "near"`, `REJECT = "reject"`
  - `evaluate(listing, profile) -> ProfileMatch`
  - `evaluate_all(listing, profiles) -> list[ProfileMatch]`
  - `matches(listing, profile) -> tuple[bool, str]` — kept, now a wrapper
  - `match_all(listing, profiles) -> list[str]` — kept, now a wrapper

`matches` and `match_all` keep their exact v1 signatures because `api.test_profiles` (`api.py:432`) and `mailbox.poll_mailbox` (`mailbox.py:174`) call them.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_filters.py`:

```python
from backend.app import filters as f

PROFILE = {
    "key": "t", "name": "Testas", "enabled": True,
    "min_price": 3000, "max_price": 20000,
    "min_plot_ares": 30, "min_house_m2": 40,
    "municipalities": ["Utenos rajono"],
    "require_any": [], "require_all": [], "exclude_any": ["dalis", "butas"],
    "sources": [], "centres": [], "radius_km": None,
    "max_lake_m": None, "max_river_m": None, "min_lake_ha": None,
}

GOOD = {"title": "Sodyba prie miško", "municipality": "Utenos rajono",
        "price_eur": 15000, "plot_ares": 50, "house_m2": 60, "source": "rinka"}


def test_clean_listing_matches():
    r = f.evaluate(GOOD, PROFILE)
    assert r.state == f.MATCH
    assert r.misses == []


def test_every_miss_is_reported_not_just_the_first():
    bad = {**GOOD, "price_eur": 90000, "plot_ares": 5, "house_m2": 10}
    r = f.evaluate(bad, PROFILE)
    fields = {m.field for m in r.misses}
    assert fields == {"price", "plot_ares", "house_m2"}
    assert r.state == f.REJECT


def test_excluded_word_is_a_hard_miss():
    r = f.evaluate({**GOOD, "title": "Parduodama dalis sodybos"}, PROFILE)
    assert r.state == f.REJECT
    assert [m.kind for m in r.misses if m.field == "exclude_any"] == [f.HARD]


def test_delta_records_how_far_over():
    r = f.evaluate({**GOOD, "price_eur": 21000}, PROFILE)
    price = next(m for m in r.misses if m.field == "price")
    assert price.delta == 1000


def test_disabled_profile_never_matches():
    r = f.evaluate(GOOD, {**PROFILE, "enabled": False})
    assert r.state == f.REJECT


def test_match_all_wrapper_returns_matching_keys():
    assert f.match_all(GOOD, [PROFILE]) == ["t"]
    assert f.match_all({**GOOD, "price_eur": 90000}, [PROFILE]) == []


def test_matches_wrapper_keeps_v1_shape():
    ok, why = f.matches(GOOD, PROFILE)
    assert ok is True and why == ""
    ok, why = f.matches({**GOOD, "price_eur": 90000}, PROFILE)
    assert ok is False and "kaina" in why
```

- [ ] **Step 2: Run it to see it fail**

```
python -m pytest backend/tests/test_filters.py -v
```

Expected: FAIL — `AttributeError: module 'backend.app.filters' has no attribute 'evaluate'`.

- [ ] **Step 3: Write the implementation**

In `backend/app/filters.py`, add the imports and types below the existing `import re`:

```python
from dataclasses import dataclass, field as dc_field

HARD, SOFT = "hard", "soft"
MATCH, NEAR, REJECT = "match", "near", "reject"


@dataclass
class Miss:
    field: str
    kind: str                     # HARD | SOFT
    text: str                     # shown in the UI, Lithuanian
    delta: float | None = None    # how far outside, in the field's own unit


@dataclass
class ProfileMatch:
    key: str
    state: str                    # MATCH | NEAR | REJECT
    misses: list[Miss] = dc_field(default_factory=list)

    def as_dict(self) -> dict:
        return {"key": self.key, "state": self.state,
                "misses": [vars(m) for m in self.misses]}
```

Then replace the whole body of `matches()` (currently `filters.py:122-214`) with the evaluator plus wrappers:

```python
def evaluate(listing: dict[str, Any], profile: dict[str, Any]) -> ProfileMatch:
    """Test a listing against a profile, reporting EVERY miss.

    v1 returned on the first failure, which made "5% over the price ceiling"
    indistinguishable from "wrong in six ways" — and discarded both. Soft misses
    are ones a slightly different profile would have accepted; hard misses are
    structural and no profile edit would rescue them.
    """
    key = profile.get("key", "")
    misses: list[Miss] = []

    def hard(fieldname: str, text: str, delta: float | None = None) -> None:
        misses.append(Miss(fieldname, HARD, text, delta))

    def soft(fieldname: str, text: str, delta: float | None = None) -> None:
        misses.append(Miss(fieldname, SOFT, text, delta))

    if not profile.get("enabled", True):
        return ProfileMatch(key, REJECT, [Miss("enabled", HARD, "profilis išjungtas")])

    hay = " ".join(filter(None, [
        _norm(listing.get("title")), _norm(listing.get("notes")),
        _norm(listing.get("locality")), _norm(listing.get("municipality")),
    ]))

    src = listing.get("source")
    allowed = profile.get("sources") or []
    if allowed and src not in allowed:
        hard("sources", f"šaltinis {src} ne profilyje")

    price = listing.get("price_eur")
    lo, hi = profile.get("min_price"), profile.get("max_price")
    if price is not None:
        if lo is not None and price < lo:
            soft("price", f"kaina {price:,.0f} < {lo:,.0f} EUR".replace(",", " "),
                 lo - price)
        if hi is not None and price > hi:
            soft("price", f"kaina {price:,.0f} > {hi:,.0f} EUR".replace(",", " "),
                 price - hi)

    munis = profile.get("municipalities") or []
    muni = listing.get("municipality")
    if munis and muni and muni not in munis:
        soft("municipality", f"{muni} ne profilio sąraše")

    plot = listing.get("plot_ares")
    need_plot = profile.get("min_plot_ares")
    if plot is not None and need_plot and plot < need_plot:
        soft("plot_ares", f"sklypas {plot:.0f} a < {need_plot:.0f} a", need_plot - plot)

    area = listing.get("house_m2")
    need_area = profile.get("min_house_m2")
    if area is not None and need_area and area < need_area:
        soft("house_m2", f"plotas {area:.0f} m2 < {need_area:.0f} m2", need_area - area)

    for w in profile.get("exclude_any") or []:
        if w.lower() in hay:
            hard("exclude_any", f"rastas draudžiamas žodis „{w}“")

    for w in profile.get("require_all") or []:
        if w.lower() not in hay:
            hard("require_all", f"trūksta privalomo žodžio „{w}“")

    misses.extend(_keyword_misses(hay, profile))
    misses.extend(_radius_misses(listing, profile))
    misses.extend(_nature_misses(listing, profile))

    return ProfileMatch(key, _state(misses), misses)


def _state(misses: list[Miss]) -> str:
    """MATCH when clean, REJECT otherwise. Task 6 introduces NEAR here."""
    return MATCH if not misses else REJECT
```

Add the three helpers. `_keyword_misses` is a placeholder shape for now — Task 5 gives it group semantics:

```python
def _keyword_misses(hay: str, profile: dict[str, Any]) -> list[Miss]:
    words = profile.get("require_any") or []
    if not words:
        return []
    if any(str(w).lower() in hay for w in words):
        return []
    return [Miss("require_any", HARD, "nerastas nė vienas raktažodis")]


def _radius_misses(listing: dict[str, Any], profile: dict[str, Any]) -> list[Miss]:
    centres = profile.get("centres") or []
    radius = profile.get("radius_km")
    if not (centres and radius):
        return []
    from .sources.nature import geocode
    from .geo import dist_m
    e, n = listing.get("easting"), listing.get("northing")
    if e is None or n is None:
        place = (geocode(listing.get("locality"), listing.get("municipality"))
                 or geocode(listing.get("municipality")))
        if not place:
            return [Miss("radius_km", HARD,
                         "vietos nustatyti nepavyko, o profilis riboja spinduliu")]
        e, n = place["easting"], place["northing"]
    best = None
    for c in centres:
        centre = geocode(c)
        if not centre:
            continue
        d = dist_m(e, n, centre["easting"], centre["northing"]) / 1000
        best = d if best is None else min(best, d)
    if best is None:
        return [Miss("radius_km", HARD, "nė vienas profilio centras neatpažintas")]
    if best > float(radius):
        return [Miss("radius_km", SOFT,
                     f"{best:.0f} km nuo artimiausio centro > {radius:.0f} km",
                     best - float(radius))]
    return []


def _nature_misses(listing: dict[str, Any], profile: dict[str, Any]) -> list[Miss]:
    nature = listing.get("nature") or {}
    lake, river = nature.get("nearest_lake"), nature.get("nearest_river")
    out: list[Miss] = []
    ml = profile.get("max_lake_m")
    if ml:
        if not lake:
            out.append(Miss("max_lake_m", SOFT, "ežero nerasta"))
        else:
            need_ha = profile.get("min_lake_ha")
            if need_ha and lake["size"] < need_ha:
                out.append(Miss("min_lake_ha", SOFT,
                                f"ežeras {lake['size']:.0f} ha < {need_ha:.0f} ha",
                                need_ha - lake["size"]))
            if lake["distance_m"] > ml:
                out.append(Miss("max_lake_m", SOFT,
                                f"ežeras {lake['distance_m']/1000:.1f} km "
                                f"> {ml/1000:.1f} km",
                                lake["distance_m"] - ml))
    mr = profile.get("max_river_m")
    if mr:
        lake_ok = bool(ml and lake and lake["distance_m"] <= ml)
        if not river:
            if not lake_ok:
                out.append(Miss("max_river_m", SOFT, "upės nerasta"))
        elif river["distance_m"] > mr and not lake_ok:
            out.append(Miss("max_river_m", SOFT,
                            f"upė {river['distance_m']/1000:.1f} km > {mr/1000:.1f} km",
                            river["distance_m"] - mr))
    return out
```

Finally add the backward-compatible wrappers, replacing the existing `match_all`:

```python
def evaluate_all(listing: dict[str, Any],
                 profiles: list[dict[str, Any]]) -> list[ProfileMatch]:
    return [evaluate(listing, p) for p in profiles]


def matches(listing: dict[str, Any], profile: dict[str, Any]) -> tuple[bool, str]:
    """v1 signature, kept for api.test_profiles."""
    r = evaluate(listing, profile)
    return r.state == MATCH, (r.misses[0].text if r.misses else "")


def match_all(listing: dict[str, Any], profiles: list[dict[str, Any]]) -> list[str]:
    """Keys of every profile this listing fully satisfies."""
    return [r.key for r in evaluate_all(listing, profiles) if r.state == MATCH]
```

- [ ] **Step 4: Run the tests**

```
python -m pytest backend/tests/ -v
```

Expected: all PASS, including Tasks 1 and 2. The full suite must stay green — `match_all` behaviour is unchanged by design.

- [ ] **Step 5: Commit**

```bash
git add backend/app/filters.py backend/tests/test_filters.py
git commit -m "refactor: filters report every miss instead of the first"
```

---

### Task 5: Keyword groups

`require_any` is one flat list, so `FOREST_WORDS` and `WATER_WORDS` cannot be distinguished. A listing with forest but no water is indistinguishable from one with neither. Groups make "miškas ✓ / vanduo ✗" expressible.

**Semantics:** every group must be hit for a match. A group miss is **soft when at least one other group was hit** (a near-relative of what you asked for) and **hard when no group was hit at all** (a different listing entirely). A v1 flat list migrates to exactly one group, where "miss the only group" means "no group hit" means hard — identical to v1 behaviour.

**Files:**
- Modify: `backend/app/filters.py` — `_keyword_misses`, `sanitise`, the `PRESETS` entries
- Modify: `backend/tests/test_filters.py`

**Interfaces:**
- Produces: `require_any` accepts two shapes — `list[str]` (v1, migrated on read) and `list[{"name": str, "words": list[str]}]` (canonical). `sanitise` always emits the canonical shape.
- Produces: `normalise_groups(raw) -> list[dict]`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_filters.py`:

```python
GROUPED = {**PROFILE, "require_any": [
    {"name": "miškas", "words": ["mišk", "giri"]},
    {"name": "vanduo", "words": ["ežer", "upė"]},
]}


def test_all_groups_hit_is_a_match():
    r = f.evaluate({**GOOD, "title": "Sodyba miške prie ežero"}, GROUPED)
    assert r.state == f.MATCH


def test_one_group_of_two_is_a_soft_miss():
    r = f.evaluate({**GOOD, "title": "Sodyba miško apsuptyje"}, GROUPED)
    m = next(x for x in r.misses if x.field == "require_any")
    assert m.kind == f.SOFT
    assert "vanduo" in m.text


def test_no_group_hit_is_a_hard_miss():
    r = f.evaluate({**GOOD, "title": "Mūrinis namas mieste"}, GROUPED)
    m = next(x for x in r.misses if x.field == "require_any")
    assert m.kind == f.HARD


def test_flat_v1_list_still_behaves_as_v1():
    flat = {**PROFILE, "require_any": ["mišk", "giri"]}
    assert f.evaluate({**GOOD, "title": "Sodyba miške"}, flat).state == f.MATCH
    r = f.evaluate({**GOOD, "title": "Namas mieste"}, flat)
    assert next(x for x in r.misses if x.field == "require_any").kind == f.HARD


def test_sanitise_upgrades_a_flat_list_to_one_group():
    p = f.sanitise({"name": "X", "require_any": ["mišk", "giri"]})
    assert p["require_any"] == [{"name": "raktažodžiai", "words": ["mišk", "giri"]}]


def test_sanitise_preserves_groups():
    p = f.sanitise({"name": "X", "require_any": [
        {"name": "vanduo", "words": ["ežer"]}]})
    assert p["require_any"] == [{"name": "vanduo", "words": ["ežer"]}]
```

- [ ] **Step 2: Run it to see it fail**

```
python -m pytest backend/tests/test_filters.py -v
```

Expected: the six new tests FAIL. `test_flat_v1_list_still_behaves_as_v1` may pass already — that is the compatibility guard.

- [ ] **Step 3: Write the implementation**

Replace `_keyword_misses` in `backend/app/filters.py`:

```python
def normalise_groups(raw: Any) -> list[dict[str, Any]]:
    """Accept both shapes of require_any and return the canonical grouped one.

    v1 stored a flat list of words. One flat list becomes one group, which under
    the all-groups-must-hit rule behaves exactly as v1 did.
    """
    if not raw:
        return []
    if all(isinstance(x, str) for x in raw):
        return [{"name": "raktažodžiai",
                 "words": [str(x).strip() for x in raw if str(x).strip()]}]
    out = []
    for g in raw:
        if isinstance(g, str):
            out.append({"name": g, "words": [g]})
            continue
        words = [str(w).strip() for w in (g.get("words") or []) if str(w).strip()]
        if words:
            out.append({"name": str(g.get("name") or "raktažodžiai").strip(),
                        "words": words})
    return out


def _keyword_misses(hay: str, profile: dict[str, Any]) -> list[Miss]:
    """Every group must be hit. Missing some is soft; missing all is hard."""
    groups = normalise_groups(profile.get("require_any"))
    if not groups:
        return []
    hit = [g for g in groups if any(w.lower() in hay for w in g["words"])]
    missed = [g for g in groups if g not in hit]
    if not missed:
        return []
    names = ", ".join(g["name"] for g in missed)
    if hit:
        got = ", ".join(g["name"] for g in hit)
        return [Miss("require_any", SOFT, f"{got} ✓ / {names} ✗")]
    return [Miss("require_any", HARD, f"nerasta: {names}")]
```

In `sanitise`, `require_any` currently falls into the list-of-strings branch. Give it its own branch — find the loop over `FIELDS` and add this case **before** the existing list branch:

```python
        if f == "require_any":
            out[f] = normalise_groups(v)
        elif f in ("municipalities", "require_all", "exclude_any",
                   "sources", "centres"):
```

(the existing `elif` chain continues unchanged; note `require_any` is removed from that tuple).

Update the two `PRESETS` entries that use keywords so the new expressiveness is actually used:

```python
        # forest_homestead
        "require_any": [{"name": "miškas", "words": FOREST_WORDS}],
```

```python
        # utilities_first
        "require_any": [{"name": "komunikacijos", "words": UTILITY_WORDS}],
```

- [ ] **Step 4: Run the tests**

```
python -m pytest backend/tests/ -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/filters.py backend/tests/test_filters.py
git commit -m "feat: keyword groups so forest-yes water-no is expressible"
```

---

### Task 6: Near-miss tolerances

Turn on the `NEAR` state.

**Files:**
- Modify: `backend/app/filters.py` — `_state`, add `TOLERANCE`
- Modify: `backend/tests/test_filters.py`

**Interfaces:**
- Produces: `TOLERANCE: dict[str, float]` — fractional slack per field
- Produces: `_state(misses, profile)` — now takes the profile, so bounds can be read for relative tolerance

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_filters.py`:

```python
def test_slightly_over_price_is_near_not_rejected():
    r = f.evaluate({**GOOD, "price_eur": 21000}, PROFILE)   # ceiling 20000, +5%
    assert r.state == f.NEAR


def test_far_over_price_is_rejected():
    r = f.evaluate({**GOOD, "price_eur": 45000}, PROFILE)
    assert r.state == f.REJECT


def test_hard_miss_is_never_near():
    r = f.evaluate({**GOOD, "price_eur": 21000, "title": "dalis sodybos"}, PROFILE)
    assert r.state == f.REJECT


def test_slightly_small_plot_is_near():
    r = f.evaluate({**GOOD, "plot_ares": 24}, PROFILE)      # needs 30, -20%
    assert r.state == f.NEAR


def test_partial_keyword_group_is_near():
    r = f.evaluate({**GOOD, "title": "Sodyba miško apsuptyje"}, GROUPED)
    assert r.state == f.NEAR


def test_match_all_still_excludes_near_misses():
    assert f.match_all({**GOOD, "price_eur": 21000}, [PROFILE]) == []
```

- [ ] **Step 2: Run it to see it fail**

```
python -m pytest backend/tests/test_filters.py -v
```

Expected: the `NEAR` assertions FAIL — `_state` currently only returns `MATCH` or `REJECT`.

- [ ] **Step 3: Write the implementation**

Add near the top of `backend/app/filters.py`, below the state constants:

```python
# How far outside a bound a listing may sit and still be shown as "Beveik".
# Fractions of the bound itself, except require_any/municipality which are
# categorical and always within tolerance when soft.
# These are first guesses. The near-miss log is what corrects them.
TOLERANCE: dict[str, float] = {
    "price": 0.25,
    "plot_ares": 0.30,
    "house_m2": 0.25,
    "max_lake_m": 0.50,
    "max_river_m": 0.50,
    "min_lake_ha": 0.40,
    "radius_km": 0.30,
}

_CATEGORICAL = {"require_any", "municipality"}


def _bound_for(fieldname: str, profile: dict[str, Any],
               listing: dict[str, Any]) -> float | None:
    """The profile bound a delta should be measured against."""
    if fieldname == "price":
        price, hi, lo = (listing.get("price_eur"), profile.get("max_price"),
                         profile.get("min_price"))
        if price is not None and hi is not None and price > hi:
            return float(hi)
        return float(lo) if lo else None
    return {
        "plot_ares": profile.get("min_plot_ares"),
        "house_m2": profile.get("min_house_m2"),
        "max_lake_m": profile.get("max_lake_m"),
        "max_river_m": profile.get("max_river_m"),
        "min_lake_ha": profile.get("min_lake_ha"),
        "radius_km": profile.get("radius_km"),
    }.get(fieldname)
```

Replace `_state` and update its single call site inside `evaluate`:

```python
def _state(misses: list[Miss], profile: dict[str, Any],
           listing: dict[str, Any]) -> str:
    """MATCH when clean, NEAR when every miss is soft and within tolerance."""
    if not misses:
        return MATCH
    if any(m.kind == HARD for m in misses):
        return REJECT
    for m in misses:
        if m.field in _CATEGORICAL:
            continue
        tol = TOLERANCE.get(m.field)
        bound = _bound_for(m.field, profile, listing)
        if tol is None or bound is None or m.delta is None:
            return REJECT
        if m.delta > bound * tol:
            return REJECT
    return NEAR
```

In `evaluate`, change the final line from

```python
    return ProfileMatch(key, _state(misses), misses)
```

to

```python
    return ProfileMatch(key, _state(misses, profile, listing), misses)
```

- [ ] **Step 4: Run the tests**

```
python -m pytest backend/tests/ -v
```

Expected: all PASS. `test_match_all_still_excludes_near_misses` is the important one — near-misses must not leak into the matched list, or Telegram will fire on them.

- [ ] **Step 5: Commit**

```bash
git add backend/app/filters.py backend/tests/test_filters.py
git commit -m "feat: near-miss state for listings just outside a profile bound"
```

---

### Task 7: Persist near-misses

Near-misses exist in memory but `mailbox.poll_mailbox` still drops anything `match_all` rejects (`mailbox.py:174-177`).

**Files:**
- Modify: `backend/app/db.py` — `SCHEMA`, `MIGRATIONS`
- Modify: `backend/app/sources/mailbox.py` — `_insert`, `_INSERT_SQL`, `poll_mailbox`
- Modify: `backend/app/api.py` — `_row_to_candidate`, `list_candidates`

**Interfaces:**
- Consumes: `filters.evaluate_all`, `filters.MATCH`, `filters.NEAR`
- Produces: `candidate.match_state` (`match` | `near`), `candidate.misses_json`
- Produces: `_row_to_candidate` output gains `match_state: str` and `misses: list[dict]`
- Produces: `GET /api/candidates?match_state=near`, defaulting to `match` only

- [ ] **Step 1: Add the schema changes**

In `backend/app/db.py`, append to `MIGRATIONS`:

```python
    ("candidate", "match_state",
     "ALTER TABLE candidate ADD COLUMN match_state TEXT NOT NULL DEFAULT 'match'"),
    ("candidate", "misses_json",
     "ALTER TABLE candidate ADD COLUMN misses_json TEXT NOT NULL DEFAULT '{}'"),
```

Add to `SCHEMA`, before the `refresh_log` block:

```sql
CREATE TABLE IF NOT EXISTS source_cursor (
    source    TEXT PRIMARY KEY,
    last_id   TEXT,
    etag      TEXT,
    modified  TEXT,
    polled_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Add the two columns to the `candidate` block in `SCHEMA` too, so fresh databases get them without relying on migrations:

```sql
    match_state       TEXT NOT NULL DEFAULT 'match',
    misses_json       TEXT NOT NULL DEFAULT '{}',
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_ingest_state.py`:

```python
import json

from backend.app.db import connect, init_db


def test_candidate_table_has_match_state_columns():
    init_db()
    with connect() as cx:
        cols = {r["name"] for r in cx.execute("PRAGMA table_info(candidate)")}
    assert "match_state" in cols
    assert "misses_json" in cols


def test_source_cursor_table_exists():
    init_db()
    with connect() as cx:
        cx.execute("INSERT OR REPLACE INTO source_cursor(source,last_id) VALUES('t','9')")
        row = cx.execute("SELECT last_id FROM source_cursor WHERE source='t'").fetchone()
    assert row["last_id"] == "9"


def test_row_to_candidate_exposes_match_state():
    from backend.app.api import _row_to_candidate
    init_db()
    with connect() as cx:
        cx.execute(
            "INSERT INTO candidate(ref,source,match_state,misses_json) "
            "VALUES('T900','rinka','near',?)",
            (json.dumps({"t": [{"field": "price", "kind": "soft",
                                "text": "kaina 21 000 > 20 000 EUR", "delta": 1000}]}),))
        row = cx.execute("SELECT * FROM candidate WHERE ref='T900'").fetchone()
    c = _row_to_candidate(row)
    assert c["match_state"] == "near"
    assert c["misses"]["t"][0]["field"] == "price"
```

- [ ] **Step 3: Run it to see it fail**

```
python -m pytest backend/tests/test_ingest_state.py -v
```

Expected: the first two PASS if Step 1 was applied, the third FAILS on `KeyError: 'match_state'`.

- [ ] **Step 4: Wire the columns through**

In `backend/app/api.py`, add two entries to the dict in `_row_to_candidate` (after the `"nature"` line):

```python
        "match_state": r["match_state"],
        "misses": json.loads(r["misses_json"] or "{}"),
```

In `list_candidates`, add the parameter after `source`:

```python
    match_state: str = "match",
```

and the clause, after the `source` filter:

```python
    if match_state != "all":
        sql += " AND match_state=?"; args.append(match_state)
```

Default `"match"` means every existing caller keeps seeing exactly what it saw before.

In `backend/app/sources/mailbox.py`, extend `_INSERT_SQL` and `_insert`. Replace the SQL:

```python
# Column list and parameter tuple must stay in lockstep — 20 bound parameters
# against 20 `?` placeholders, with flags/scores/checks defaulted inline and
# `archived` fixed at 0. Count both sides if you touch this.
_INSERT_SQL = (
    "INSERT INTO candidate("
    "ref,source,url,title,municipality,locality,cadastral_no,price_eur,house_m2,"
    "plot_ares,auction_ends_at,flags_json,scores_json,costs_json,checks_json,"
    "notes,fingerprint,profiles_json,easting,northing,nature_json,"
    "match_state,misses_json,archived) "
    "VALUES(?,?,?,?,?,?,?,?,?,?,?,'{}','{}',?,'{}',?,?,?,?,?,?,?,?,0)"
)
```

Change `_insert`'s signature and body:

```python
def _insert(listing: dict[str, Any], hits: list[str], fp: str,
            state: str = "match", misses: dict[str, Any] | None = None) -> str | None:
```

and add the two values to the parameter tuple, immediately after the `json.dumps(nature, ...)` entry:

```python
            state,
            json.dumps(misses or {}, ensure_ascii=False),
```

Replace the filter block in `poll_mailbox` (currently `mailbox.py:174-183`):

```python
                from ..filters import evaluate_all, MATCH, NEAR
                results = evaluate_all(listing, profiles)
                hits = [r.key for r in results if r.state == MATCH]
                nears = [r for r in results if r.state == NEAR]
                if not hits and not nears:
                    rejected += 1
                    continue
                state = "match" if hits else "near"
                misses = {r.key: [vars(m) for m in r.misses]
                          for r in results if r.state in (MATCH, NEAR)}
                ref = _insert(listing, hits or [r.key for r in nears],
                              _fingerprint(listing), state, misses)
                if ref and hits:
                    created.append({"ref": ref, "profiles": hits, **{
                        k: listing.get(k) for k in
                        ("title", "municipality", "locality", "price_eur",
                         "house_m2", "plot_ares", "url", "source")}})
```

`created` is what `main.scheduled_mailbox` hands to `notify.push`, so near-misses are stored but never pushed — deliberate, and the reason `if ref and hits` guards the append.

Remove the now-unused `from ..filters import match_all` at `mailbox.py:27`.

- [ ] **Step 5: Run the tests**

```
python -m pytest backend/tests/ -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/db.py backend/app/api.py backend/app/sources/mailbox.py backend/tests/test_ingest_state.py
git commit -m "feat: store near-miss listings instead of discarding them"
```

---

### Task 8: Cross-source dedupe

With three ingest paths one sodyba arrives three times. `mailbox._fingerprint` is URL-based, so the same property from rinka, an aruodas alert, and a paste produces three candidates.

**Files:**
- Create: `backend/app/dedupe.py`, `backend/tests/test_dedupe.py`
- Modify: `backend/app/sources/mailbox.py` — use the shared module

**Interfaces:**
- Produces:
  - `fingerprint(listing: dict) -> str` — moved verbatim from `mailbox._fingerprint`
  - `title_tokens(title: str | None) -> set[str]`
  - `is_duplicate(a: dict, b: dict) -> bool`
  - `find_duplicate(listing: dict, rows: list[dict]) -> dict | None`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_dedupe.py`:

```python
from backend.app import dedupe

A = {"source": "rinka", "municipality": "Utenos rajono", "locality": "Kirdeikių k.",
     "price_eur": 17000, "house_m2": 81.3, "plot_ares": 20,
     "title": "Parduodama sodyba Utenos r. prie ežero", "cadastral_no": None}


def test_same_listing_from_another_portal_is_a_duplicate():
    b = {**A, "source": "aruodas",
         "title": "Sodyba prie ežero, Utenos r., parduodama"}
    assert dedupe.is_duplicate(A, b)


def test_small_price_drop_is_still_the_same_listing():
    assert dedupe.is_duplicate(A, {**A, "source": "alio", "price_eur": 16600})


def test_different_property_is_not_a_duplicate():
    b = {**A, "source": "aruodas", "price_eur": 9000, "house_m2": 40,
         "title": "Namas Zarasų rajone", "municipality": "Zarasų rajono"}
    assert not dedupe.is_duplicate(A, b)


def test_same_cadastral_number_beats_every_other_signal():
    a = {**A, "cadastral_no": "4400/0123:45"}
    b = {"source": "turtas", "cadastral_no": "4400/0123:45", "price_eur": 1,
         "title": "visai kitas tekstas", "municipality": "Kitas rajonas"}
    assert dedupe.is_duplicate(a, b)


def test_different_municipality_is_never_a_duplicate():
    assert not dedupe.is_duplicate(A, {**A, "municipality": "Zarasų rajono"})


def test_find_duplicate_picks_the_match_from_a_list():
    rows = [{**A, "price_eur": 5000, "title": "kitas"},
            {**A, "source": "aruodas", "ref": "K007"}]
    assert dedupe.find_duplicate(A, rows)["ref"] == "K007"


def test_find_duplicate_returns_none_when_nothing_matches():
    assert dedupe.find_duplicate(A, [{**A, "municipality": "Zarasų rajono"}]) is None


def test_fingerprint_is_stable_and_ignores_query_strings():
    x = {"url": "https://www.rinka.lt/skelbimas/foo-id-1?utm_source=x"}
    y = {"url": "https://www.rinka.lt/skelbimas/foo-id-1"}
    assert dedupe.fingerprint(x) == dedupe.fingerprint(y)
```

- [ ] **Step 2: Run it to see it fail**

```
python -m pytest backend/tests/test_dedupe.py -v
```

Expected: FAIL — `ModuleNotFoundError: backend.app.dedupe`.

- [ ] **Step 3: Write the implementation**

`backend/app/dedupe.py`:

```python
"""Identity across ingest paths. PURE.

The same sodyba legitimately arrives three times once polling, alerts and paste
all feed the same table. URLs differ per portal, so identity has to come from
the property itself. A cadastral number settles it outright; otherwise it is
municipality plus agreeing numbers plus overlapping title words.

Tolerances are loose on price (portals lag each other, and sellers cut) and
tight on municipality (never guessed, and cheap to compare).
"""
from __future__ import annotations
import hashlib
import re
from typing import Any

PRICE_TOL = 0.05      # 5% — covers a price cut between two portals' snapshots
AREA_TOL = 0.05
PLOT_TOL = 0.10
TITLE_OVERLAP = 0.34  # Jaccard over words of 4+ characters

_WORD_RE = re.compile(r"[0-9a-ząčęėįšųūž]{4,}", re.I)
_STOP = {"parduodama", "parduodamas", "parduodu", "sodyba", "sodybą", "sodybos",
         "namas", "namą", "skelbimas", "rajone", "rajono"}


def fingerprint(listing: dict[str, Any]) -> str:
    """Stable identity for one source's own re-sends. Query strings ignored."""
    if listing.get("url"):
        base = re.sub(r"[?#].*$", "", listing["url"])
    else:
        base = "|".join(str(listing.get(k) or "") for k in
                        ("source", "municipality", "locality", "price_eur", "house_m2"))
    return hashlib.sha1(base.encode()).hexdigest()[:16]


def title_tokens(title: str | None) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(title or "")} - _STOP


def _close(a: Any, b: Any, tol: float) -> bool:
    """True when both are absent, or both present and within tol of each other."""
    if a is None or b is None:
        return a is None and b is None
    a, b = float(a), float(b)
    if a == 0 and b == 0:
        return True
    return abs(a - b) <= max(abs(a), abs(b)) * tol


def is_duplicate(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ca, cb = a.get("cadastral_no"), b.get("cadastral_no")
    if ca and cb:
        return str(ca).strip() == str(cb).strip()

    if (a.get("municipality") or "") != (b.get("municipality") or ""):
        return False
    if not _close(a.get("price_eur"), b.get("price_eur"), PRICE_TOL):
        return False
    if not _close(a.get("house_m2"), b.get("house_m2"), AREA_TOL):
        return False
    if not _close(a.get("plot_ares"), b.get("plot_ares"), PLOT_TOL):
        return False

    ta, tb = title_tokens(a.get("title")), title_tokens(b.get("title"))
    if not ta or not tb:
        return True          # numbers agree and neither title is usable
    overlap = len(ta & tb) / len(ta | tb)
    return overlap >= TITLE_OVERLAP


def find_duplicate(listing: dict[str, Any],
                   rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for r in rows:
        if is_duplicate(listing, r):
            return r
    return None
```

- [ ] **Step 4: Run the tests**

```
python -m pytest backend/tests/test_dedupe.py -v
```

Expected: all 8 PASS.

- [ ] **Step 5: Use it in the mailbox path**

In `backend/app/sources/mailbox.py`, delete the local `_fingerprint` function (lines 68-75) and import the shared one:

```python
from ..dedupe import fingerprint as _fingerprint, find_duplicate
```

In `_insert`, replace the existing duplicate check inside the `with connect() as cx:` block:

```python
        if cx.execute("SELECT 1 FROM candidate WHERE fingerprint=?", (fp,)).fetchone():
            return None
        siblings = [dict(r) for r in cx.execute(
            "SELECT id,ref,cadastral_no,municipality,price_eur,house_m2,plot_ares,title "
            "FROM candidate WHERE municipality IS ? OR cadastral_no IS NOT NULL",
            (listing.get("municipality"),)).fetchall()]
        twin = find_duplicate(listing, siblings)
        if twin:
            # Same property, another portal. Keep the first, record the second URL.
            if listing.get("url"):
                cx.execute(
                    "UPDATE candidate SET notes = COALESCE(notes,'') || ? , "
                    "updated_at=datetime('now') WHERE id=?",
                    (f"\n[dublikatas {listing.get('source')}] {listing['url']}",
                     twin["id"]))
            return None
```

- [ ] **Step 6: Run the full suite**

```
python -m pytest backend/tests/ -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/dedupe.py backend/app/sources/mailbox.py backend/tests/test_dedupe.py
git commit -m "feat: collapse the same property arriving from several sources"
```

---

### Task 9: rinka.lt adapter

PURE parse functions only — no network. Verified against the live site on 2026-08-10: listing URLs are `https://www.rinka.lt/skelbimas/<slug>-id-<N>` with numeric descending IDs, pages are server-rendered HTML, price renders as `Kaina: 60000,00 &euro;` inside `<span class="price">`, and the municipality appears in the `<h1>`.

**Files:**
- Create: `backend/app/sources/adapters/__init__.py`, `backend/app/sources/adapters/rinka.py`
- Create: `backend/tests/test_rinka.py`, `backend/tests/fixtures/rinka_category.html`, `backend/tests/fixtures/rinka_detail.html`

**Interfaces:**
- Produces, in `adapters/rinka.py`:
  - `KEY = "rinka"`
  - `list_url(page: int = 1, per_page: int = 200) -> str`
  - `list_ids(html: str) -> list[tuple[int, str]]` — `(id, url)`, descending, deduped
  - `parse_detail(html: str, url: str) -> dict` — listing dict in `parsers` shape
- Produces, in `adapters/__init__.py`: `ADAPTERS: dict[str, module]`, `get(key)`

- [ ] **Step 1: Save the fixtures**

Two files, containing the markup shapes observed live. Keep them small — they test extraction, not layout.

`backend/tests/fixtures/rinka_category.html`:

```html
<html><body>
<a href="https://www.rinka.lt/nekilnojamojo-turto-skelbimai/parduodamos-sodybos?page=1&amp;per_page=200">200</a>
<a href="https://www.rinka.lt/skelbimas/isskirtine-sodyba-vienkemis-id-5080474">A</a>
<a href="https://www.rinka.lt/skelbimas/parduodu-sodyba-id-5078893">B</a>
<a href="https://www.rinka.lt/skelbimas/parduodu-sodyba-id-5078893">B again</a>
<a href="https://www.rinka.lt/skelbimas/sodyba-85-ha-sklype-id-5078522">C</a>
<a href="https://www.rinka.lt/login">login</a>
</body></html>
```

`backend/tests/fixtures/rinka_detail.html`:

```html
<html><head><title>x</title></head><body>
<nav><select>
  <option>Akmenės r.</option><option>Anykščių r.</option><option>Biržų r.</option>
</select></nav>
<h1>Parduodama sodyba Alytaus r. netoli Žuvinto ežero</h1>
<div class="rightBlock pull-right">
  <span class="price"> Kaina: 60000,00 &euro; <span class="priceInfo">x</span></span>
</div>
<div class="description">
  Sodyba 20 arų sklype, namo plotas 81,28 m2, ūkinis pastatas 75,88 m2.
  Sklypas 0,2028 ha. Kadastro Nr. 0101/0022:38
</div>
</body></html>
```

- [ ] **Step 2: Write the failing test**

`backend/tests/test_rinka.py`:

```python
import pathlib

from backend.app.sources.adapters import rinka

FIX = pathlib.Path(__file__).parent / "fixtures"
CATEGORY = (FIX / "rinka_category.html").read_text(encoding="utf-8")
DETAIL = (FIX / "rinka_detail.html").read_text(encoding="utf-8")
URL = "https://www.rinka.lt/skelbimas/parduodama-sodyba-id-5076782"


def test_list_url_is_paginated():
    assert rinka.list_url(1, 200).endswith("?page=1&per_page=200")


def test_list_ids_finds_listings_only():
    ids = rinka.list_ids(CATEGORY)
    assert [i for i, _ in ids] == [5080474, 5078893, 5078522]


def test_list_ids_deduplicates():
    assert len(rinka.list_ids(CATEGORY)) == 3


def test_list_ids_returns_absolute_urls():
    assert all(u.startswith("https://www.rinka.lt/skelbimas/")
               for _, u in rinka.list_ids(CATEGORY))


def test_parse_detail_reads_the_price():
    assert rinka.parse_detail(DETAIL, URL)["price_eur"] == 60000.0


def test_parse_detail_reads_title_and_source():
    d = rinka.parse_detail(DETAIL, URL)
    assert d["title"].startswith("Parduodama sodyba Alytaus r.")
    assert d["source"] == "rinka"
    assert d["url"] == URL


def test_municipality_comes_from_the_heading_not_the_nav_dropdown():
    # The nav lists every municipality; naive extraction returns Akmenės.
    assert rinka.parse_detail(DETAIL, URL)["municipality"] == "Alytaus rajono"


def test_parse_detail_reads_areas():
    d = rinka.parse_detail(DETAIL, URL)
    assert d["house_m2"] == 81.28
    assert d["plot_ares"] == 20
```

- [ ] **Step 3: Run it to see it fail**

```
python -m pytest backend/tests/test_rinka.py -v
```

Expected: FAIL — `ModuleNotFoundError: backend.app.sources.adapters`.

- [ ] **Step 4: Write the implementation**

`backend/app/sources/adapters/__init__.py`:

```python
"""Per-site adapters. Each exposes list_url, list_ids and parse_detail, all PURE.

Fetching belongs to poller.py so that every parser is testable against saved
HTML with no network and no politeness concerns.
"""
from . import rinka

ADAPTERS = {rinka.KEY: rinka}


def get(key: str):
    return ADAPTERS.get(key)
```

`backend/app/sources/adapters/rinka.py`:

```python
"""rinka.lt extraction. PURE — no I/O.

robots.txt is `User-agent: * / Disallow:` — fully open. Verified 2026-08-10 and
recorded in sources/registry.py.

Structure verified against a live listing the same day:
  * listing URLs are /skelbimas/<slug>-id-<N>, N numeric and descending, which
    is why the poller only needs a high-water mark;
  * price renders as `Kaina: 60000,00 &euro;` inside <span class="price">;
  * the page nav lists every municipality in the country, so municipality is
    taken from the <h1> and the content block, never from the whole document.
"""
from __future__ import annotations
import re
from typing import Any

from .. import parsers

KEY = "rinka"
BASE = "https://www.rinka.lt"
CATEGORY = "/nekilnojamojo-turto-skelbimai/parduodamos-sodybos"

_LINK_RE = re.compile(r'href="(https://www\.rinka\.lt/skelbimas/[^"?#]*?-id-(\d+))"')
_H1_RE = re.compile(r"(?is)<h1[^>]*>(.*?)</h1>")
_CONTENT_MARK = 'class="price"'


def list_url(page: int = 1, per_page: int = 200) -> str:
    return f"{BASE}{CATEGORY}?page={page}&per_page={per_page}"


def list_ids(html: str) -> list[tuple[int, str]]:
    """Listing ids and urls, newest first, deduplicated."""
    seen: dict[int, str] = {}
    for url, num in _LINK_RE.findall(html or ""):
        seen.setdefault(int(num), url)
    return sorted(seen.items(), key=lambda kv: -kv[0])


def _content(html: str) -> str:
    """Everything from the price block onward — excludes the nav dropdown."""
    i = html.find(_CONTENT_MARK)
    return html[i:] if i >= 0 else html


def parse_detail(html: str, url: str) -> dict[str, Any]:
    body = parsers.to_text(_content(html))
    d = parsers._common(body)

    h1 = _H1_RE.search(html or "")
    title = parsers.to_text(h1.group(1)).strip() if h1 else parsers._title(body)
    d["title"] = (title or "")[:180] or None

    # Municipality: heading first, content block second. Never the whole page.
    muni = parsers.MUNI_RE.search(title or "") or parsers.MUNI_RE.search(body)
    d["municipality"] = f"{muni.group(1)} rajono" if muni else None

    d["source"] = KEY
    d["url"] = url
    d["raw"] = body[:4000]
    return d
```

- [ ] **Step 5: Run the tests**

```
python -m pytest backend/tests/test_rinka.py -v
```

Expected: all 8 PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/sources/adapters/ backend/tests/test_rinka.py backend/tests/fixtures/
git commit -m "feat: rinka.lt adapter with pure parse functions"
```

---

### Task 10: The poller

The only new code that touches the network. Every fetch passes `registry.assert_pollable` first.

**Files:**
- Create: `backend/app/sources/poller.py`, `backend/tests/test_poller.py`
- Modify: `backend/app/config.py`, `backend/app/sources/__init__.py`

**Interfaces:**
- Consumes: `registry.assert_pollable`, `adapters.get`, `filters.evaluate_all`, `dedupe.fingerprint`, `mailbox._insert`
- Produces:
  - `async poll_source(key: str, fetch=None, limit: int = 40) -> dict` — keys `status`, `created`, `scanned`, `rejected`
  - `async poll_all(fetch=None) -> dict` — `{source_key: result}`
  - `POLLED = ["rinka"]`
- Produces in `config.py`: `POLL_MINUTES: int`, `POLL_MAX_PER_RUN: int`

`fetch` is an injectable async callable `(url: str) -> tuple[int, str]`. Default is httpx.

- [ ] **Step 1: Add configuration**

In `backend/app/config.py`, after the mailbox block:

```python
# ---------------------------------------------------------------- poller
# Only sources declared POLL in sources/registry.py are ever fetched.
POLL_MINUTES = int(os.getenv("SR_POLL_MINUTES", "60"))
POLL_MAX_PER_RUN = int(os.getenv("SR_POLL_MAX_PER_RUN", "40"))
```

- [ ] **Step 2: Write the failing test**

`backend/tests/test_poller.py`:

```python
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
```

- [ ] **Step 3: Run it to see it fail**

```
python -m pytest backend/tests/test_poller.py -v
```

Expected: FAIL — `ModuleNotFoundError: backend.app.sources.poller`.

- [ ] **Step 4: Write the implementation**

`backend/app/sources/poller.py`:

```python
"""The polling ingest path.

Only sources declared POLL in registry.py are reachable: assert_pollable runs
before any connection is opened, and it refuses unknown keys as well as
forbidden ones, so adding a portal without first reading its robots.txt fails
loudly rather than silently scraping it.

Listings enter exactly the same pipeline as email alerts — locate, filter,
dedupe, insert — so there is one scorer and one notion of identity.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx

from ..config import HTTP_TIMEOUT, HTTP_UA, POLL_MAX_PER_RUN
from ..db import connect, log_refresh
from . import registry as reg
from . import adapters
from .mailbox import _insert

log = logging.getLogger(__name__)

POLLED = ["rinka"]

Fetch = Callable[[str], Awaitable[tuple[int, str]]]


async def _http_fetch(url: str) -> tuple[int, str]:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT,
                                 headers={"User-Agent": HTTP_UA},
                                 follow_redirects=True) as cx:
        r = await cx.get(url)
        return r.status_code, r.text


def _cursor(source: str) -> int:
    with connect() as cx:
        row = cx.execute("SELECT last_id FROM source_cursor WHERE source=?",
                         (source,)).fetchone()
    try:
        return int(row["last_id"]) if row and row["last_id"] else 0
    except (TypeError, ValueError):
        return 0


def _save_cursor(source: str, last_id: int) -> None:
    with connect() as cx:
        cx.execute(
            "INSERT INTO source_cursor(source,last_id,polled_at) "
            "VALUES(?,?,datetime('now')) ON CONFLICT(source) DO UPDATE SET "
            "last_id=excluded.last_id, polled_at=datetime('now')",
            (source, str(last_id)))


def _profiles() -> list[dict[str, Any]]:
    from ..db import get_setting
    from ..filters import PRESETS
    return [p for p in (get_setting("filter_profiles") or PRESETS)
            if p.get("enabled", True)]


async def poll_source(key: str, fetch: Fetch | None = None,
                      limit: int = POLL_MAX_PER_RUN) -> dict[str, Any]:
    """One polling pass over one source. Raises PolicyError if not permitted."""
    source = reg.assert_pollable(key)          # before anything else
    adapter = adapters.get(key)
    if adapter is None:
        raise reg.PolicyError(f"„{key}“ leidžiamas, bet adapteris neparašytas")

    fetch = fetch or _http_fetch
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    profiles = _profiles()
    since = _cursor(key)
    created: list[dict[str, Any]] = []
    scanned = rejected = 0
    high = since

    try:
        status, html = await fetch(adapter.list_url())
        if status != 200:
            log_refresh(key, "error", f"sąrašo puslapis grąžino {status}", 0, started)
            return {"status": "error", "http_status": status}

        fresh = [(i, u) for i, u in adapter.list_ids(html) if i > since][:limit]
        for listing_id, url in fresh:
            await asyncio.sleep(source.crawl_delay_s)
            st, page = await fetch(url)
            if st != 200:
                continue
            scanned += 1
            listing = adapter.parse_detail(page, url)
            if listing.get("price_eur") is None and listing.get("house_m2") is None:
                continue

            from ..advisor import assess_nature       # local import: avoids a cycle
            from ..filters import evaluate_all, MATCH, NEAR
            from ..dedupe import fingerprint

            listing["nature"] = assess_nature(listing)
            results = evaluate_all(listing, profiles)
            hits = [r.key for r in results if r.state == MATCH]
            nears = [r.key for r in results if r.state == NEAR]
            if not hits and not nears:
                rejected += 1
                high = max(high, listing_id)
                continue
            misses = {r.key: [vars(m) for m in r.misses]
                      for r in results if r.state in (MATCH, NEAR)}
            ref = _insert(listing, hits or nears, fingerprint(listing),
                          "match" if hits else "near", misses)
            if ref and hits:
                created.append({"ref": ref, "profiles": hits, **{
                    k: listing.get(k) for k in
                    ("title", "municipality", "locality", "price_eur",
                     "house_m2", "plot_ares", "url", "source")}})
            high = max(high, listing_id)

        if high > since:
            _save_cursor(key, high)
    except reg.PolicyError:
        raise
    except Exception as exc:
        log.exception("%s poll failed", key)
        log_refresh(key, "error", str(exc)[:400], 0, started)
        return {"status": "error", "error": str(exc)}

    detail = f"{len(created)} nauji; peržiūrėta {scanned}; atmesta {rejected}"
    log_refresh(key, "ok", detail, len(created), started)
    log.info("%s: %s", key, detail)
    return {"status": "ok", "created": created,
            "scanned": scanned, "rejected": rejected}


async def poll_all(fetch: Fetch | None = None) -> dict[str, Any]:
    """Poll every source with an adapter. One failure does not stop the rest."""
    out: dict[str, Any] = {}
    for key in POLLED:
        try:
            out[key] = await poll_source(key, fetch=fetch)
        except Exception as exc:
            log.exception("%s poll raised", key)
            out[key] = {"status": "error", "error": str(exc)}
    return out
```

Extend `backend/app/sources/__init__.py`:

```python
from .poller import poll_source, poll_all, POLLED  # noqa: F401
from . import registry  # noqa: F401
```

- [ ] **Step 5: Run the tests**

```
python -m pytest backend/tests/ -v
```

Expected: all PASS. `test_second_run_fetches_nothing_new` is the one that matters — without the watermark the poller re-fetches every listing on every run, which is exactly the impolite behaviour the registry exists to prevent.

- [ ] **Step 6: Commit**

```bash
git add backend/app/sources/poller.py backend/app/sources/__init__.py backend/app/config.py backend/tests/test_poller.py
git commit -m "feat: policy-gated poller with per-source watermark and crawl delay"
```

---

### Task 11: Wire the poller into the app

**Files:**
- Modify: `backend/app/api.py` — poll route, schema block
- Modify: `backend/app/main.py` — scheduler job, stale-registry warning

**Interfaces:**
- Produces: `POST /api/ingest/poll` → `{source: result}`
- Produces: `GET /api/schema` gains `ingest.sources` and `ingest.stale_sources`

- [ ] **Step 1: Add the route**

In `backend/app/api.py`, extend the import at line 15:

```python
from .sources import (refresh_market_stock, poll_mailbox, mailbox_configured,
                      refresh_water, refresh_places, refresh_protected, geocode,
                      poll_all, POLLED, registry)
```

Add after `ingest_mailbox`:

```python
@router.post("/ingest/poll")
async def ingest_poll() -> dict[str, Any]:
    """Poll every permitted source now. Sources are gated by sources/registry.py."""
    result = await poll_all()
    created = [c for r in result.values() for c in (r.get("created") or [])]
    if created:
        await notify.push(created)
    return result
```

In `schema()`, replace the `"ingest"` block:

```python
        "ingest": {
            "mailbox_configured": mailbox_configured(),
            "telegram_configured": notify.enabled(),
            "layers": _layer_status(),
            "polled": POLLED,
            "sources": [
                {"key": s.key, "host": s.host, "policy": s.policy,
                 "robots": s.robots, "checked_at": s.checked_at}
                for s in registry.SOURCES
            ],
        },
```

- [ ] **Step 2: Add the scheduler job**

In `backend/app/main.py`, extend the config import:

```python
from .config import (DEFAULT_MUNICIPALITIES, FRONTEND_DIR, MAILBOX_POLL_MINUTES,
                     POLL_MINUTES, REFRESH_CRON_HOUR, REFRESH_ON_BOOT)
```

and the sources import:

```python
from .sources import (refresh_market_stock, poll_mailbox, mailbox_configured,
                      refresh_water, refresh_places, refresh_protected,
                      poll_all, registry)
```

Add the job function next to `scheduled_mailbox`:

```python
async def scheduled_poll() -> None:
    """Poll the sources whose robots.txt permits it."""
    try:
        result = await poll_all()
        created = [c for r in result.values() for c in (r.get("created") or [])]
        if created:
            await notify.push(created)
            log.info("poller: %s new candidates", len(created))
    except Exception:
        log.exception("source poll failed")
```

In `lifespan`, after the mailbox job registration:

```python
    sched.add_job(scheduled_poll, IntervalTrigger(minutes=POLL_MINUTES),
                  id="source_poll", max_instances=1, coalesce=True)
    log.info("source polling every %s min: %s", POLL_MINUTES,
             ", ".join(s.key for s in registry.SOURCES
                       if s.policy == registry.POLL))

    stale = registry.stale(date.today().isoformat())
    if stale:
        log.warning("robots.txt verdicts older than 90 days: %s — recheck before "
                    "trusting the poller", ", ".join(stale))
```

Add the import at the top of `main.py`:

```python
from datetime import date
```

- [ ] **Step 3: Verify by hand**

```
python -m pytest backend/tests/ -v
uvicorn backend.app.main:app --port 8000
```

In a second terminal:

```
curl -s http://127.0.0.1:8000/api/schema | python -m json.tool | grep -A3 '"polled"'
curl -s -X POST http://127.0.0.1:8000/api/ingest/poll | python -m json.tool
curl -s "http://127.0.0.1:8000/api/candidates?match_state=near" | python -m json.tool | head -30
```

Expected: `schema` lists `rinka` under `polled` and every source with its robots verdict. The poll returns `{"rinka": {"status": "ok", ...}}`. The first real run downloads nature layers first if they are missing — allow three to four minutes.

Expected in the log: `source polling every 60 min: data_gov, rinka, zudc, ntaukcionai, adminbiuras, turtas` and no stale warning.

- [ ] **Step 4: Commit**

```bash
git add backend/app/api.py backend/app/main.py
git commit -m "feat: schedule source polling and expose the policy registry"
```

---

### Task 12: The "Beveik" tier in the dashboard

**Files:**
- Modify: `frontend/index.html` — filter control, table header
- Modify: `frontend/app.js` — `filterQuery`, `loadCandidates`
- Modify: `frontend/styles.css` — near-miss row styling

**Interfaces:**
- Consumes: `c.match_state`, `c.misses` from `/api/candidates`; `match_state` query parameter

- [ ] **Step 1: Add the filter control**

In `frontend/index.html`, after the `fVerdict` label block (around line 90), add:

```html
      <label class="field">
        <span>Atitikimas</span>
        <select id="fMatchState">
          <option value="match">Tik atitinkantys</option>
          <option value="near">Tik „beveik“</option>
          <option value="all">Visi</option>
        </select>
      </label>
```

In the table header (line 169), add a column before `<th>Verdiktas</th>`:

```html
              <th>Atitikimas</th>
```

- [ ] **Step 2: Send the parameter**

In `frontend/app.js`, inside `filterQuery()`, before `return p.toString();`:

```javascript
  put('match_state', $('fMatchState').value);
```

- [ ] **Step 3: Render the tier**

In `loadCandidates()`, add after the `VERDICT_LT` constant block near line 23:

```javascript
const MATCH_LT = { match: 'Atitinka', near: 'Beveik' };
```

Inside the `data.items.forEach` loop, add the cell immediately before the verdict cell (`app.js:147`):

```javascript
    const misses = Object.values(c.misses || {}).flat();
    const why = misses.map((m) => m.text).join(' · ');
    tr.appendChild(td(
      `<span class="tag ${c.match_state}" title="${esc(why)}">` +
      `${MATCH_LT[c.match_state] || c.match_state}</span>` +
      (c.match_state === 'near' && why
        ? `<small class="miss">${esc(why)}</small>` : '')));
```

and mark the row, next to the existing archived check:

```javascript
    if (c.match_state === 'near') tr.classList.add('is-near');
```

- [ ] **Step 4: Style it**

Append to `frontend/styles.css`:

```css
/* Near-miss rows: present, visibly secondary, never mistaken for a match. */
tr.is-near { opacity: .72; }
tr.is-near:hover { opacity: 1; }
.tag.near { background: #6b5b2e; color: #f5e9c8; }
.tag.match { background: #2e5b3a; color: #d9f0df; }
small.miss {
  display: block;
  margin-top: .2rem;
  font-size: .74rem;
  line-height: 1.25;
  color: var(--muted, #9aa0a6);
}
```

- [ ] **Step 5: Verify in the browser**

```
uvicorn backend.app.main:app --reload --port 8000
```

Open `http://127.0.0.1:8000`, then:

1. Set **Atitikimas** to *Visi* — near-misses appear, dimmed, each with its reason beneath.
2. Set it to *Tik „beveik“* — only near-misses.
3. Leave it at the default *Tik atitinkantys* — the table looks exactly as it did before this task.

If there are no near-misses yet, create one: open the **Profilis** tab, lower a profile's `max_price` to just under a stored candidate's price, save, then re-poll.

- [ ] **Step 6: Commit**

```bash
git add frontend/index.html frontend/app.js frontend/styles.css
git commit -m "feat: Beveik tier showing listings that just missed a profile"
```

---

## Done when

- `python -m pytest backend/tests/ -v` is green — 50-odd tests across eight files.
- `POST /api/ingest/poll` returns `{"rinka": {"status": "ok", ...}}` and real listings land in the table without any email having arrived.
- A second poll immediately after fetches no detail pages.
- Adding `Source("aruodas", ...)` to `POLLED` raises `PolicyError` instead of fetching.
- The dashboard's default view is unchanged; **Atitikimas → Visi** reveals near-misses with reasons.

## Deliberately not in this plan

- **Location precision and buildability** — Plan B. Until then `assess_nature` still measures from a settlement centroid at ±1 km, and polled listings inherit that.
- **Seller type (private vs agency).** rinka.lt shows a seller block and a phone number, but the private/company markers were not verified on 2026-08-10 and `AGENT.md` is explicit that speculative parser work is how this codebase gets bugs. Add it after reading real pages.
- **Adapters for zudc, ntaukcionai, adminbiuras, turtas.** All four are declared `POLL` in the registry and all four are refused by `poll_source` until someone writes an adapter — deliberately, and the error says so. Each needs its own structural survey first, exactly as rinka got here.
- **Near-miss calibration loop** (§6 of the spec: "six of your last twenty near-misses were price-over-by-under-15%"). Needs near-miss history to exist first. Plan C.
- **Near-miss retention** (spec §6: "archived after 30 days, configurable"). `source_cursor` and `misses_json` make it trivial to add, but the right retention window depends on near-miss volume, which nobody knows until the poller has run for a fortnight. Setting it now would be a guess dressed as a policy. Revisit with real numbers.
- **Conditional GET.** `source_cursor` carries `etag` and `modified` columns, deliberately unused. The watermark already stops detail pages being refetched, which is where the request volume is; the one category page an hour is negligible either way. The columns exist so adding it later needs no migration.
- **The `1.234.567 EUR` case** in `parsers._f`, which raises `ValueError` on two thousands separators. Pre-existing, unreachable in the 5-60k band, out of scope.
