# Intake Widening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Poll rinka.lt's `parduodami-namai` category alongside `parduodamos-sodybos`, without any listing being silently skipped.

**Architecture:** The lawfulness gate stays keyed by host — `registry.py` is untouched, because robots.txt is a property of `www.rinka.lt`, not of a path. Categories become an adapter concern: `rinka.py` declares a category→path map and `list_url` takes a category. The poller iterates categories, each with its own cursor in a new table, each paginating until it has enough. One category failing does not stop the others.

**Tech Stack:** Python 3.12, SQLite (WAL), httpx, pytest. No new dependencies.

Spec: `docs/superpowers/specs/2026-08-12-intake-widening-design.md`

## Global Constraints

- PURE modules do no I/O. `adapters/rinka.py` stays PURE — fetching belongs to `poller.py`.
- Migrations are additive only. New tables go in `db.SCHEMA` using `CREATE TABLE IF NOT EXISTS`; new columns go in `db.MIGRATIONS` as `(table, column, ddl)` tuples.
- `sources/registry.py` must not be modified by this plan. `assert_pollable(key)` keeps its present meaning.
- User-facing strings are Lithuanian. Code, comments and test names are English.
- No UTF-8 BOM in any file. Use ASCII escapes (` `) for non-breaking spaces in source, never a literal NBSP.
- Every network fetch waits `source.crawl_delay_s` first — rinka declares 2.0 s. This now applies to list pages too, not only detail pages.
- Commit messages end, after a blank line, with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
- Run the full suite with `python -m pytest` from the repo root. It must pass at the end of every task. Baseline at plan start: 535 passing.

---

### Task 1: rinka adapter learns categories

**Files:**
- Modify: `backend/app/sources/adapters/rinka.py:41` (the `CATEGORY` constant) and `:110-111` (`list_url`)
- Test: `backend/tests/test_rinka_categories.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `rinka.CATEGORIES: dict[str, str]` — category key → site path
  - `rinka.list_url(category: str, page: int = 1, per_page: int = 200) -> str`
  - `rinka.UnknownCategory` — raised by `list_url` for a key not in `CATEGORIES`

`list_ids` and `parse_detail` are **not** touched. In particular do not touch `_content()`: its heading-boundary slicing and its bound at the first link to a different listing id are two earned fix rounds, and they are category-agnostic.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_rinka_categories.py`:

```python
"""rinka.lt serves several categories from one host under one robots policy."""
import pytest

from backend.app.sources.adapters import rinka


def test_both_categories_are_declared():
    assert set(rinka.CATEGORIES) == {"sodybos", "namai"}


def test_sodybos_path_is_unchanged():
    # The path this project has polled since day one. Changing it would
    # orphan the existing cursor and re-walk 86 listings.
    assert rinka.CATEGORIES["sodybos"] == (
        "/nekilnojamojo-turto-skelbimai/parduodamos-sodybos")


def test_namai_path():
    assert rinka.CATEGORIES["namai"] == (
        "/nekilnojamojo-turto-skelbimai/parduodami-namai")


def test_list_url_builds_the_category_path():
    assert rinka.list_url("namai", page=2, per_page=200) == (
        "https://www.rinka.lt/nekilnojamojo-turto-skelbimai/"
        "parduodami-namai?page=2&per_page=200")


def test_list_url_defaults_to_page_one():
    assert rinka.list_url("sodybos").endswith("?page=1&per_page=200")


def test_unknown_category_raises_rather_than_falling_back():
    # Silently defaulting to sodybos would make a typo look like a working
    # poll that quietly searched the wrong category.
    with pytest.raises(rinka.UnknownCategory):
        rinka.list_url("butai")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest backend/tests/test_rinka_categories.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'CATEGORIES'`

- [ ] **Step 3: Implement**

In `backend/app/sources/adapters/rinka.py`, replace the `CATEGORY` constant (line 41) with:

```python
# One host, one robots.txt, one crawl delay -- so one registry entry (see
# sources/registry.py). Categories are a path concern and live here, which is
# why adding one costs nothing and cannot weaken the policy check.
CATEGORIES = {
    "sodybos": "/nekilnojamojo-turto-skelbimai/parduodamos-sodybos",
    "namai": "/nekilnojamojo-turto-skelbimai/parduodami-namai",
}


class UnknownCategory(KeyError):
    """A category key this adapter does not declare."""
```

and replace `list_url` (lines 110-111) with:

```python
def list_url(category: str, page: int = 1, per_page: int = 200) -> str:
    try:
        path = CATEGORIES[category]
    except KeyError:
        raise UnknownCategory(category) from None
    return f"{BASE}{path}?page={page}&per_page={per_page}"
```

Update the module docstring's structure notes to say the adapter serves several
categories from one host.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest backend/tests/test_rinka_categories.py -v`
Expected: PASS (6 tests)

Then run the full suite: `python -m pytest`
Expected: some existing tests calling `list_url()` with no argument now fail. Fix each by passing `"sodybos"` explicitly — do not add a default category, because a default is what makes a typo silent.

- [ ] **Step 5: Commit**

```bash
git add backend/app/sources/adapters/rinka.py backend/tests/test_rinka_categories.py
git commit -m "Let the rinka adapter name the category it is reading"
```

---

### Task 2: a cursor per category

**Files:**
- Modify: `backend/app/db.py` — add the table to `SCHEMA` (near `source_cursor`, around line 100)
- Modify: `backend/app/sources/poller.py:40-56` (`_cursor`, `_save_cursor`)
- Test: `backend/tests/test_category_cursor.py` (create)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `poller._cursor(source: str, category: str) -> int`
  - `poller._save_cursor(source: str, category: str, last_id: int) -> None`

**Why this task exists:** `source_cursor`'s primary key is `source`. Point one high-water mark at two id streams and the higher suppresses the lower — a `namai` listing numbered below the sodybos watermark is filtered out by `if i_u[0] > since` and never retried. That is the *silently lose a listing* failure. SQLite cannot alter a primary key, so this is a new table, not an altered one.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_category_cursor.py`:

```python
"""Each category carries its own high-water mark."""
from backend.app.db import connect, init_db
from backend.app.sources import poller


def test_absent_cursor_reads_as_zero():
    assert poller._cursor("rinka", "namai_absent_test") == 0


def test_each_category_advances_independently():
    poller._save_cursor("rinka", "cat_a", 5_000_000)
    poller._save_cursor("rinka", "cat_b", 4_000_000)
    assert poller._cursor("rinka", "cat_a") == 5_000_000
    # The lower stream must NOT be dragged up by the higher one. This is the
    # whole reason the table exists.
    assert poller._cursor("rinka", "cat_b") == 4_000_000


def test_saving_twice_updates_rather_than_duplicating():
    poller._save_cursor("rinka", "cat_c", 1)
    poller._save_cursor("rinka", "cat_c", 2)
    assert poller._cursor("rinka", "cat_c") == 2
    with connect() as cx:
        n = cx.execute(
            "SELECT COUNT(*) n FROM source_category_cursor "
            "WHERE source='rinka' AND category='cat_c'").fetchone()["n"]
    assert n == 1


def test_a_malformed_stored_value_reads_as_zero():
    with connect() as cx:
        cx.execute(
            "INSERT INTO source_category_cursor(source,category,last_id) "
            "VALUES('rinka','cat_junk','not-a-number') "
            "ON CONFLICT(source,category) DO UPDATE SET last_id=excluded.last_id")
    assert poller._cursor("rinka", "cat_junk") == 0


def test_sodybos_is_seeded_from_the_old_single_cursor():
    # A database that has been polling since before categories existed must
    # resume where it left off, not re-walk the whole category.
    with connect() as cx:
        cx.execute("DELETE FROM source_category_cursor "
                   "WHERE source='seedtest' AND category='sodybos'")
        cx.execute("INSERT INTO source_cursor(source,last_id) VALUES('seedtest','4991510') "
                   "ON CONFLICT(source) DO UPDATE SET last_id=excluded.last_id")
    init_db()          # re-runs SCHEMA, which carries the seed statement
    assert poller._cursor("seedtest", "sodybos") == 4991510


def test_seeding_is_idempotent_and_does_not_clobber_progress():
    with connect() as cx:
        cx.execute("INSERT INTO source_cursor(source,last_id) VALUES('seedtest2','100') "
                   "ON CONFLICT(source) DO UPDATE SET last_id=excluded.last_id")
    init_db()
    poller._save_cursor("seedtest2", "sodybos", 999)
    init_db()          # a later boot must not reset it back to 100
    assert poller._cursor("seedtest2", "sodybos") == 999
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest backend/tests/test_category_cursor.py -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: source_category_cursor`

- [ ] **Step 3: Implement the table**

In `backend/app/db.py`, in `SCHEMA`, immediately after the `source_cursor` table:

```sql
-- One high-water mark per (source, category). source_cursor held one per
-- source, which was correct while every source read a single category: point
-- it at two id streams and the higher one filters out everything below it in
-- the lower stream, losing those listings permanently.
CREATE TABLE IF NOT EXISTS source_category_cursor (
    source    TEXT NOT NULL,
    category  TEXT NOT NULL,
    last_id   TEXT,
    etag      TEXT,
    modified  TEXT,
    polled_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (source, category)
);

-- Carry an existing single cursor forward as that source's original category
-- so it resumes instead of re-walking. Guarded, so re-running SCHEMA on every
-- boot cannot reset a cursor that has since advanced.
INSERT INTO source_category_cursor(source, category, last_id, etag, modified, polled_at)
SELECT source, 'sodybos', last_id, etag, modified, polled_at
FROM source_cursor c
WHERE NOT EXISTS (
    SELECT 1 FROM source_category_cursor sc
    WHERE sc.source = c.source AND sc.category = 'sodybos'
);
```

`source_cursor` stays in place and is no longer read by the poller. Dropping it is not additive and buys nothing.

- [ ] **Step 4: Implement the accessors**

In `backend/app/sources/poller.py`, replace `_cursor` and `_save_cursor` (lines 40-56):

```python
def _cursor(source: str, category: str) -> int:
    with connect() as cx:
        row = cx.execute(
            "SELECT last_id FROM source_category_cursor "
            "WHERE source=? AND category=?", (source, category)).fetchone()
    try:
        return int(row["last_id"]) if row and row["last_id"] else 0
    except (TypeError, ValueError):
        return 0


def _save_cursor(source: str, category: str, last_id: int) -> None:
    with connect() as cx:
        cx.execute(
            "INSERT INTO source_category_cursor(source,category,last_id,polled_at) "
            "VALUES(?,?,?,datetime('now')) "
            "ON CONFLICT(source,category) DO UPDATE SET "
            "last_id=excluded.last_id, polled_at=datetime('now')",
            (source, category, str(last_id)))
```

Update the two call sites in `poll_source` to pass a category. Task 3 restructures that function properly; for now pass `"sodybos"` so the suite stays green.

- [ ] **Step 5: Run the tests**

Run: `python -m pytest backend/tests/test_category_cursor.py -v` then `python -m pytest`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/db.py backend/app/sources/poller.py backend/tests/test_category_cursor.py
git commit -m "Give every category its own high-water mark"
```

---

### Task 3: the poller reads every category, page by page

**Files:**
- Modify: `backend/app/sources/poller.py` — `poll_source`, plus a new `_poll_category` helper
- Modify: `backend/app/config.py` — add `POLL_MAX_PAGES`
- Test: `backend/tests/test_poll_categories.py` (create)

**Interfaces:**
- Consumes: `rinka.CATEGORIES`, `rinka.list_url(category, page, per_page)` (Task 1); `_cursor(source, category)`, `_save_cursor(source, category, id)` (Task 2).
- Produces: `poll_source(key, fetch=None, limit=POLL_MAX_PER_RUN)` returning
  `{"status": "ok", "created": [...], "scanned": int, "rejected": int, "categories": {<category>: {"scanned": int, "rejected": int, "created": int, "status": str}}}`

**Preserve exactly:** the contiguous-advance rule. The cursor may only advance across the unbroken run of successes *before* the first stall, per category. The existing comment block at lines 97-112 explains why; keep it, scoped per category.

- [ ] **Step 1: Add the page cap to config**

In `backend/app/config.py`, beside `POLL_MAX_PER_RUN`:

```python
# A category is walked page by page until it yields nothing new. The cap stops
# a pathological or looping response from spinning forever, and a run that hits
# it says so rather than looking complete.
POLL_MAX_PAGES = int(os.getenv("SR_POLL_MAX_PAGES", "5"))
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_poll_categories.py`:

```python
"""Multi-category polling: every category advances on its own, and a listing
below another category's watermark is still ingested."""
import pytest

from backend.app.sources import poller
from backend.app.sources.adapters import rinka


def _listing_page(ids):
    """A category listing page carrying links in rinka's real shape."""
    return "".join(
        f'<a href="https://www.rinka.lt/skelbimas/x-id-{i}">x</a>' for i in ids)


DETAIL = (
    '<h1>Sodyba</h1><span class="price">Kaina: 9000,00 &euro;</span>'
    "<div>Sklypas: 50,00 a. Utenos r. Antažilių k.</div>")


def _fetcher(pages, detail=DETAIL):
    """pages: {url_substring: (status, body)}. Records call order."""
    calls = []

    async def fetch(url):
        calls.append(url)
        for frag, resp in pages.items():
            if frag in url:
                return resp
        return (200, detail)

    return fetch, calls


@pytest.mark.asyncio
async def test_a_namai_id_below_the_sodybos_watermark_is_still_ingested(monkeypatch):
    # The bug this whole change exists to prevent. sodybos is far ahead;
    # namai must not inherit its watermark.
    poller._save_cursor("rinka", "sodybos", 5_000_000)
    poller._save_cursor("rinka", "namai", 0)
    fetch, calls = _fetcher({
        "parduodamos-sodybos": (200, _listing_page([5_000_001])),
        "parduodami-namai": (200, _listing_page([4_000_001])),
    })
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    out = await poller.poll_source("rinka", fetch=fetch, limit=10)
    assert out["status"] == "ok"
    fetched = [c for c in calls if "/skelbimas/" in c]
    assert any("id-4000001" in c for c in fetched), \
        "the low-numbered namai listing was skipped"
    assert poller._cursor("rinka", "namai") == 4_000_001
    assert poller._cursor("rinka", "sodybos") == 5_000_001


@pytest.mark.asyncio
async def test_pagination_walks_until_a_page_yields_nothing_new(monkeypatch):
    poller._save_cursor("rinka", "sodybos", 0)
    poller._save_cursor("rinka", "namai", 0)
    pages = {
        "parduodami-namai?page=1": (200, _listing_page([100, 101])),
        "parduodami-namai?page=2": (200, _listing_page([102])),
        "parduodami-namai?page=3": (200, _listing_page([])),
        "parduodamos-sodybos": (200, _listing_page([])),
    }
    fetch, calls = _fetcher(pages)
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    await poller.poll_source("rinka", fetch=fetch, limit=50)
    assert any("page=2" in c for c in calls)
    assert any("page=3" in c for c in calls)
    assert not any("page=4" in c for c in calls)


@pytest.mark.asyncio
async def test_page_walking_stops_once_the_run_limit_is_reached(monkeypatch):
    poller._save_cursor("rinka", "sodybos", 0)
    poller._save_cursor("rinka", "namai", 0)
    pages = {
        "parduodami-namai?page=1": (200, _listing_page(range(200, 210))),
        "parduodamos-sodybos": (200, _listing_page([])),
    }
    fetch, calls = _fetcher(pages)
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    await poller.poll_source("rinka", fetch=fetch, limit=3)
    # No point fetching page 2 when this run will not process its ids.
    assert not any("parduodami-namai?page=2" in c for c in calls)


@pytest.mark.asyncio
async def test_max_pages_caps_a_never_ending_category(monkeypatch):
    poller._save_cursor("rinka", "sodybos", 0)
    poller._save_cursor("rinka", "namai", 0)
    monkeypatch.setattr(poller, "POLL_MAX_PAGES", 2)
    counter = {"n": 300}

    async def fetch(url):
        if "parduodamos-sodybos" in url:
            return (200, _listing_page([]))
        if "/skelbimas/" in url:
            return (200, DETAIL)
        counter["n"] += 1                     # every page looks fresh
        return (200, _listing_page([counter["n"]]))

    monkeypatch.setattr(poller, "_profiles", lambda: [])
    out = await poller.poll_source("rinka", fetch=fetch, limit=500)
    assert out["categories"]["namai"]["pages_capped"] is True


@pytest.mark.asyncio
async def test_one_category_failing_does_not_stop_the_other(monkeypatch):
    poller._save_cursor("rinka", "sodybos", 0)
    poller._save_cursor("rinka", "namai", 7_000_000)
    fetch, calls = _fetcher({
        "parduodamos-sodybos": (503, ""),
        "parduodami-namai": (200, _listing_page([7_000_001])),
    })
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    out = await poller.poll_source("rinka", fetch=fetch, limit=10)
    assert out["categories"]["sodybos"]["status"] == "error"
    assert out["categories"]["namai"]["status"] == "ok"
    # A failed list page must leave its cursor exactly where it was.
    assert poller._cursor("rinka", "sodybos") == 0
    assert poller._cursor("rinka", "namai") == 7_000_001


@pytest.mark.asyncio
async def test_the_crawl_delay_is_awaited_with_the_registry_value(monkeypatch):
    # Lawfulness toward a site that permits crawling. Previously verified only
    # by hand -- see deferred finding B4.
    poller._save_cursor("rinka", "sodybos", 0)
    poller._save_cursor("rinka", "namai", 0)
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(poller.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(poller, "_profiles", lambda: [])
    fetch, _ = _fetcher({
        "parduodamos-sodybos": (200, _listing_page([10])),
        "parduodami-namai": (200, _listing_page([11])),
    })
    await poller.poll_source("rinka", fetch=fetch, limit=10)
    assert slept, "no crawl delay was awaited"
    assert set(slept) == {2.0}, f"expected rinka's declared 2.0s, got {set(slept)}"
```

Note: this project's async tests use `pytest.mark.asyncio`. Follow whatever the existing async tests in `backend/tests/` do — match their idiom exactly rather than introducing a second one.

- [ ] **Step 3: Run it to verify it fails**

Run: `python -m pytest backend/tests/test_poll_categories.py -v`
Expected: FAIL — `poll_source` calls `adapter.list_url()` with no category, and the return value has no `"categories"` key.

- [ ] **Step 4: Implement**

Restructure `poll_source` in `backend/app/sources/poller.py`. Extract the per-category work into a helper, keeping the existing detail-loop body and its comments intact:

```python
async def _poll_category(source, adapter, key: str, category: str,
                         fetch: Fetch, limit: int,
                         profiles: list[dict[str, Any]]) -> dict[str, Any]:
    """One category of one source. Its cursor is its own."""
    since = _cursor(key, category)
    fresh: dict[int, str] = {}
    pages_capped = False

    for page in range(1, POLL_MAX_PAGES + 1):
        await asyncio.sleep(source.crawl_delay_s)
        status, html = await fetch(adapter.list_url(category, page))
        if status != 200:
            # Cursor untouched: this category is simply skipped this run.
            # `created` is a list on every path -- poll_source extends with it.
            return {"status": "error", "http_status": status,
                    "scanned": 0, "rejected": 0, "created": [],
                    "pages_capped": False}
        page_ids = [i_u for i_u in adapter.list_ids(html) if i_u[0] > since]
        if not page_ids:
            break
        fresh.update(dict(page_ids))          # same id on two pages counts once
        if len(fresh) >= limit:
            break
        if page == POLL_MAX_PAGES:
            pages_capped = True

    # ids arrive newest-first. Process the OLDEST new ones first so the cursor
    # advances contiguously: a batch bigger than `limit` is then caught up over
    # successive runs instead of having its tail skipped.
    batch = sorted(fresh.items())[:limit]
    ...
```

Then move the existing detail loop verbatim into this helper, with three changes:

1. `high = since` and the `stalled_at` logic stay exactly as they are — they now scope to this category.
2. Set the provenance before insert: `listing["source_category"] = category` (Task 4 stores it; setting it here is harmless until then).
3. `if high > since: _save_cursor(key, category, high)`.

Return `{"status": "ok", "scanned": …, "rejected": …, "created": [...], "pages_capped": pages_capped}`.

`poll_source` becomes the loop over categories:

```python
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
    per_category: dict[str, Any] = {}
    created: list[dict[str, Any]] = []
    scanned = rejected = 0

    try:
        for category in adapter.CATEGORIES:
            res = await _poll_category(source, adapter, key, category,
                                       fetch, limit, profiles)
            per_category[category] = {k: v for k, v in res.items() if k != "created"}
            per_category[category]["created"] = len(res.get("created") or [])
            created.extend(res.get("created") or [])
            scanned += res.get("scanned", 0)
            rejected += res.get("rejected", 0)
    except reg.PolicyError:
        raise
    except Exception as exc:
        log.exception("%s poll failed", key)
        log_refresh(key, "error", str(exc)[:400], 0, started)
        return {"status": "error", "error": str(exc)}

    parts = ", ".join(f"{c}: {r['scanned']}" for c, r in per_category.items())
    detail = f"{len(created)} nauji; peržiūrėta {scanned} ({parts}); atmesta {rejected}"
    capped = [c for c, r in per_category.items() if r.get("pages_capped")]
    if capped:
        detail += f"; puslapių riba pasiekta: {', '.join(capped)}"
    failed = [c for c, r in per_category.items() if r["status"] == "error"]
    if failed:
        detail += f"; nepavyko: {', '.join(failed)}"
    log_refresh(key, "ok", detail, len(created), started)
    log.info("%s: %s", key, detail)
    return {"status": "ok", "created": created, "scanned": scanned,
            "rejected": rejected, "categories": per_category}
```

Keep the per-category stall message from the original `detail` string — carry `stalled_at` out of the helper and mention the category it stalled in.

- [ ] **Step 5: Run the tests**

Run: `python -m pytest backend/tests/test_poll_categories.py -v` then `python -m pytest`
Expected: all pass. Existing poller tests asserting the old flat return shape will need their assertions updated to the new `categories` key — update them, do not delete them.

- [ ] **Step 6: Commit**

```bash
git add backend/app/sources/poller.py backend/app/config.py backend/tests/test_poll_categories.py
git commit -m "Poll every category a source declares, each at its own pace"
```

---

### Task 4: record which category a listing came from

**Files:**
- Modify: `backend/app/db.py` — one entry in `MIGRATIONS`
- Modify: `backend/app/sources/mailbox.py:127-134` (the `INSERT` constant) and `_insert`
- Modify: `backend/app/api.py` — include `source_category` in the candidate payload
- Test: `backend/tests/test_source_category.py` (create)

**Interfaces:**
- Consumes: `listing["source_category"]` set by `_poll_category` (Task 3).
- Produces: `candidate.source_category` column; `source_category` on `/api/candidates` rows.

**The lockstep:** the `INSERT` constant at `mailbox.py:127` currently names **27 columns** with **23 `?`** placeholders and is called with **23 parameters**. Adding a column means all three move together. Append `source_category` immediately before `archived` in the column list, add one `?` immediately after the `misses_json` placeholder, and add the value in the matching position of the params tuple. Count all three afterwards: 28 / 24 / 24.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_source_category.py`:

```python
"""Provenance: which of a source's categories a listing came from."""
from backend.app.db import connect
from backend.app.dedupe import fingerprint
from backend.app.sources.mailbox import _insert


def _listing(**over):
    d = {"source": "rinka", "url": "https://www.rinka.lt/skelbimas/a-id-1",
         "title": "Sodyba", "municipality": "Utenos rajono", "locality": "Antalieptė",
         "price_eur": 9000.0, "house_m2": 60.0, "plot_ares": 40.0, "notes": ""}
    d.update(over)
    return d


def _column(ref, name):
    with connect() as cx:
        row = cx.execute(f"SELECT {name} FROM candidate WHERE ref=?", (ref,)).fetchone()
    return row[name] if row else None


def test_the_category_is_stored():
    li = _listing(url="https://www.rinka.lt/skelbimas/cat-a-id-90001",
                  source_category="namai")
    ref = _insert(li, ["p"], fingerprint(li))
    assert ref is not None
    assert _column(ref, "source_category") == "namai"


def test_a_listing_without_a_category_stores_null_not_a_guess():
    # The email path has no category. Inventing one would be a confident wrong
    # value -- the failure this project ranks first.
    li = _listing(url="https://www.rinka.lt/skelbimas/cat-b-id-90002")
    ref = _insert(li, ["p"], fingerprint(li))
    assert _column(ref, "source_category") is None


def test_the_insert_column_and_placeholder_counts_agree():
    # The INSERT is one string constant; a column added without its placeholder
    # fails only at runtime, on a real ingest.
    from backend.app.sources import mailbox
    sql = mailbox.INSERT if hasattr(mailbox, "INSERT") else None
    assert sql, "name the INSERT constant so it can be asserted on"
    cols = sql.split("INSERT INTO candidate(")[1].split(")")[0]
    values = sql.split("VALUES(")[1].rsplit(")", 1)[0]
    assert len(cols.split(",")) == len(values.split(","))
```

If the `INSERT` constant is currently private (`_INSERT`), use that name in the
test rather than renaming it.

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest backend/tests/test_source_category.py -v`
Expected: FAIL — `sqlite3.OperationalError: no such column: source_category`

- [ ] **Step 3: Implement**

In `backend/app/db.py`, append to `MIGRATIONS`:

```python
    # Nullable, never defaulted, and never back-filled: rows ingested before
    # categories existed genuinely have no recorded category, and labelling
    # them by inference would be a guess wearing the clothes of a fact.
    ("candidate", "source_category",
     "ALTER TABLE candidate ADD COLUMN source_category TEXT"),
```

In `backend/app/sources/mailbox.py`, update the `INSERT` constant per the lockstep note above, and in `_insert` pass `listing.get("source_category")` in the matching parameter position.

In `backend/app/api.py`, add `source_category` to the candidate row payload wherever `source` is already emitted (`_row_to_candidate` / the `/api/candidates` serialiser).

- [ ] **Step 4: Run the tests**

Run: `python -m pytest backend/tests/test_source_category.py -v` then `python -m pytest`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/app/sources/mailbox.py backend/app/api.py backend/tests/test_source_category.py
git commit -m "Record which category a listing came from, and nothing more"
```

---

### Task 5: prove one homestead listed twice becomes one candidate

**Files:**
- Test: `backend/tests/test_cross_category_dedupe.py` (create)
- Modify: only if a defect is found.

**Interfaces:**
- Consumes: everything from Tasks 1-4.
- Produces: no new code. This task proves existing behaviour under a case that could not previously occur.

**Why:** one property can be listed under both `sodybos` and `namai` with different listing ids. `dedupe.py` fingerprints on price, area and locality with 4-character Lithuanian stemming, and `mailbox._insert` folds a duplicate into its twin — but neither has ever seen two rows from the same source under different categories, because until Task 3 that was impossible. If this task finds a defect, fix it; if it does not, the tests are still the point.

- [ ] **Step 1: Write the test**

Create `backend/tests/test_cross_category_dedupe.py`:

```python
"""The same homestead advertised under two categories is one candidate."""
from backend.app.db import connect
from backend.app.dedupe import fingerprint
from backend.app.sources.mailbox import _insert


def _twin(url, category, **over):
    d = {"source": "rinka", "url": url, "title": "Sodyba prie ežero",
         "municipality": "Utenos rajono", "locality": "Antalieptė",
         "price_eur": 12000.0, "house_m2": 70.0, "plot_ares": 55.0,
         "source_category": category, "notes": ""}
    d.update(over)
    return d


def _count(locality):
    with connect() as cx:
        return cx.execute(
            "SELECT COUNT(*) n FROM candidate WHERE locality=? AND archived=0",
            (locality,)).fetchone()["n"]


def test_the_same_property_under_two_categories_yields_one_candidate():
    loc = "Dvigubasŭkė"
    a = _twin("https://www.rinka.lt/skelbimas/x-id-95001", "sodybos", locality=loc)
    _insert(a, ["p"], fingerprint(a))
    b = _twin("https://www.rinka.lt/skelbimas/y-id-95002", "namai", locality=loc)
    _insert(b, ["p"], fingerprint(b))
    assert _count(loc) == 1


def test_the_merge_leaves_a_visible_trail():
    # Deferred findings E2/E3: a wrong merge must be recoverable by inspection.
    loc = "Pėdsakas"
    a = _twin("https://www.rinka.lt/skelbimas/x-id-95003", "sodybos", locality=loc)
    _insert(a, ["p"], fingerprint(a))
    b = _twin("https://www.rinka.lt/skelbimas/y-id-95004", "namai", locality=loc)
    _insert(b, ["p"], fingerprint(b))
    with connect() as cx:
        notes = cx.execute("SELECT notes FROM candidate WHERE locality=?",
                           (loc,)).fetchone()["notes"]
    assert "95004" in (notes or ""), "the discarded listing's URL is not recorded"


def test_two_genuinely_different_properties_are_not_merged():
    a = _twin("https://www.rinka.lt/skelbimas/x-id-95005", "sodybos",
              locality="SkirtingaĀ", price_eur=12000.0)
    _insert(a, ["p"], fingerprint(a))
    b = _twin("https://www.rinka.lt/skelbimas/y-id-95006", "namai",
              locality="SkirtingaĀ", price_eur=24000.0, plot_ares=8.0)
    _insert(b, ["p"], fingerprint(b))
    assert _count("SkirtingaĀ") == 2
```

- [ ] **Step 2: Run it**

Run: `python -m pytest backend/tests/test_cross_category_dedupe.py -v`
Expected: PASS if the existing dedupe already covers this. If any fail, that is a real defect uncovered by the widening — fix it in `dedupe.py` or `mailbox._insert`, keeping `STEM_LEN = 4` (deferred finding E1/E2 explains why 5 breaks `ežero`/`ežeras`).

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_cross_category_dedupe.py
git commit -m "Prove one homestead listed under two categories stays one candidate"
```

---

## Verification before deployment

- [ ] `python -m pytest` passes in full.
- [ ] Run one real poll against rinka locally with a small limit and confirm from the log line that both categories were walked, and that the delay between requests is ~2 s:
      `python -c "import asyncio,sys; sys.path.insert(0,'.'); from backend.app.sources.poller import poll_source; print(asyncio.run(poll_source('rinka', limit=3)))"`
- [ ] Confirm `source_category_cursor` holds a row per category and that `sodybos` resumed from its previous value rather than 0.
- [ ] Confirm existing candidates still read `source_category IS NULL` and no row was back-labelled.

## Deployment

Backend change: `git pull`, `chown -R sodyba:sodyba /opt/sodyba`, `systemctl restart sodyba-radar`. Never run `docker compose up -d` on that box — it would start a second Caddy on :80/:443 and take down every site on the server.
