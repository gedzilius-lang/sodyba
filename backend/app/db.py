"""SQLite access layer. One connection per request, WAL for concurrent reads."""
from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from .config import DB_PATH

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS candidate (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ref               TEXT UNIQUE NOT NULL,
    source            TEXT NOT NULL,
    url               TEXT,
    title             TEXT,
    municipality      TEXT,
    locality          TEXT,
    cadastral_no      TEXT,
    price_eur         REAL,
    house_m2          REAL,
    plot_ares         REAL,
    auction_ends_at   TEXT,
    listed_at         TEXT,
    contact_phone     TEXT,
    contact_email     TEXT,
    flags_json        TEXT NOT NULL DEFAULT '{}',
    scores_json       TEXT NOT NULL DEFAULT '{}',
    costs_json        TEXT NOT NULL DEFAULT '{}',
    checks_json       TEXT NOT NULL DEFAULT '{}',
    notes             TEXT,
    fingerprint       TEXT,
    easting           REAL,
    northing          REAL,
    nature_json       TEXT NOT NULL DEFAULT '{}',
    profiles_json     TEXT NOT NULL DEFAULT '[]',
    match_state       TEXT NOT NULL DEFAULT 'match',
    misses_json       TEXT NOT NULL DEFAULT '{}',
    source_category   TEXT,
    archived          INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_candidate_muni ON candidate(municipality);
CREATE INDEX IF NOT EXISTS ix_candidate_archived ON candidate(archived);
CREATE UNIQUE INDEX IF NOT EXISTS ux_candidate_fp ON candidate(fingerprint)
    WHERE fingerprint IS NOT NULL;

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS market_stock (
    municipality      TEXT PRIMARY KEY,
    total             INTEGER,
    with_power        INTEGER,
    with_water        INTEGER,
    power_and_water   INTEGER,
    pre_1945          INTEGER,
    log_walls         INTEGER,
    fetched_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS water_feature (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    kind     TEXT NOT NULL,
    name     TEXT,
    easting  REAL NOT NULL,
    northing REAL NOT NULL,
    size     REAL,
    cell_x   INTEGER NOT NULL,
    cell_y   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_water_cell ON water_feature(kind, cell_x, cell_y);

CREATE TABLE IF NOT EXISTS place (
    code         INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    municipality TEXT,
    easting      REAL NOT NULL,
    northing     REAL NOT NULL,
    area_ha      REAL,
    cell_x       INTEGER NOT NULL,
    cell_y       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_place_name ON place(name);
CREATE INDEX IF NOT EXISTS ix_place_cell ON place(cell_x, cell_y);

CREATE TABLE IF NOT EXISTS protected_area (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    kind    TEXT NOT NULL,
    name    TEXT,
    area_ha REAL,
    min_e   REAL, min_n REAL, max_e REAL, max_n REAL
);

CREATE TABLE IF NOT EXISTS source_cursor (
    source    TEXT PRIMARY KEY,
    last_id   TEXT,
    etag      TEXT,
    modified  TEXT,
    polled_at TEXT NOT NULL DEFAULT (datetime('now'))
);

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
-- rinka only: it is the one source whose single cursor was built by walking
-- parduodamos-sodybos, so it is the one whose category is known. Labelling
-- every source's cursor 'sodybos' would invent a provenance -- and the day a
-- second adapter declares a sodybos category, that invented row would sit as
-- a high watermark on a category never polled, suppressing exactly the
-- listings this table exists to stop losing.
INSERT INTO source_category_cursor(source, category, last_id, etag, modified, polled_at)
SELECT source, 'sodybos', last_id, etag, modified, polled_at
FROM source_cursor c
WHERE c.source = 'rinka' AND NOT EXISTS (
    SELECT 1 FROM source_category_cursor sc
    WHERE sc.source = c.source AND sc.category = 'sodybos'
);

-- Listings the poller could not ingest, and what became of them.
--
-- The contiguous-advance rule in poller._poll_category is what stops a
-- listing being silently skipped: once an id fails, that category's cursor
-- may not pass it, so it is offered again next run. A listing that fails
-- PERMANENTLY -- deleted between the category page and the detail fetch, or
-- a page this parser will never understand -- therefore pins its category at
-- that id for good. That is what happened in production: sodybos stuck at
-- 4992805, namai at 4924114, every run, refetching the same head hourly.
--
-- So the poller gives up after SR_POLL_GIVE_UP_AFTER consecutive failures and
-- steps over the id. Giving up is only acceptable if it is visible, and this
-- table is the record: which listing, which category, its URL, how many times
-- it failed, why it failed last, and when the poller stopped. Nothing is
-- dropped silently -- GET /api/ingest/abandoned reads this, and the run's own
-- log line names the ids as they are abandoned.
--
-- `failures` counts CONSECUTIVE failures: a run that reads the page deletes
-- the row, so one bad afternoon does not leave a listing permanently closer
-- to being abandoned. The rule stays intact -- a listing is stepped over only
-- once it has been tried, recorded and reported, never merely skipped.
CREATE TABLE IF NOT EXISTS poll_failure (
    source      TEXT NOT NULL,
    category    TEXT NOT NULL,
    listing_id  INTEGER NOT NULL,
    url         TEXT,
    failures    INTEGER NOT NULL DEFAULT 0,
    reason      TEXT,
    first_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_at     TEXT NOT NULL DEFAULT (datetime('now')),
    given_up_at TEXT,
    PRIMARY KEY (source, category, listing_id)
);
CREATE INDEX IF NOT EXISTS ix_poll_failure_given_up
    ON poll_failure(source, category, given_up_at);

CREATE TABLE IF NOT EXISTS refresh_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    status     TEXT NOT NULL,
    detail     TEXT,
    rows       INTEGER,
    started_at TEXT NOT NULL,
    ended_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


MIGRATIONS = [
    ("candidate", "fingerprint", "ALTER TABLE candidate ADD COLUMN fingerprint TEXT"),
    ("candidate", "easting", "ALTER TABLE candidate ADD COLUMN easting REAL"),
    ("candidate", "northing", "ALTER TABLE candidate ADD COLUMN northing REAL"),
    ("candidate", "nature_json",
     "ALTER TABLE candidate ADD COLUMN nature_json TEXT NOT NULL DEFAULT '{}'"),
    ("candidate", "profiles_json",
     "ALTER TABLE candidate ADD COLUMN profiles_json TEXT NOT NULL DEFAULT '[]'"),
    ("candidate", "match_state",
     "ALTER TABLE candidate ADD COLUMN match_state TEXT NOT NULL DEFAULT 'match'"),
    ("candidate", "misses_json",
     "ALTER TABLE candidate ADD COLUMN misses_json TEXT NOT NULL DEFAULT '{}'"),
    ("candidate", "listed_at", "ALTER TABLE candidate ADD COLUMN listed_at TEXT"),
    # Nullable and never defaulted: an absent contact must read as "the page
    # did not carry one", never as an empty string that looks like data.
    # api.update_candidate sets both back to NULL when a candidate is archived
    # -- see the retention comment there.
    ("candidate", "contact_phone", "ALTER TABLE candidate ADD COLUMN contact_phone TEXT"),
    ("candidate", "contact_email", "ALTER TABLE candidate ADD COLUMN contact_email TEXT"),
    # Nullable, never defaulted, and never back-filled: rows ingested before
    # categories existed genuinely have no recorded category, and labelling
    # them by inference would be a guess wearing the clothes of a fact.
    ("candidate", "source_category",
     "ALTER TABLE candidate ADD COLUMN source_category TEXT"),
]


def init_db() -> None:
    """Create the schema, then apply additive migrations for older databases."""
    with connect() as cx:
        cx.executescript(SCHEMA)
        for table, column, ddl in MIGRATIONS:
            cols = {r["name"] for r in cx.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                cx.execute(ddl)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    cx = sqlite3.connect(DB_PATH, timeout=30)
    cx.row_factory = sqlite3.Row
    try:
        yield cx
        cx.commit()
    except Exception:
        cx.rollback()
        raise
    finally:
        cx.close()


def get_setting(key: str, default: Any = None) -> Any:
    with connect() as cx:
        row = cx.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row["value_json"]) if row else default


def set_setting(key: str, value: Any) -> None:
    with connect() as cx:
        cx.execute(
            "INSERT INTO settings(key, value_json, updated_at) VALUES(?,?,datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=datetime('now')",
            (key, json.dumps(value, ensure_ascii=False)),
        )


def log_refresh(source: str, status: str, detail: str, rows: int, started_at: str) -> None:
    with connect() as cx:
        cx.execute(
            "INSERT INTO refresh_log(source,status,detail,rows,started_at) VALUES(?,?,?,?,?)",
            (source, status, detail, rows, started_at),
        )
