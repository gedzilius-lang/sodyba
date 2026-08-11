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
