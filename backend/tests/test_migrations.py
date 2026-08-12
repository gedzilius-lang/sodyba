"""Additive migrations must reach a database that already holds real rows.

The production database on the VPS was created by an earlier schema and is
full. New columns therefore arrive only through db.MIGRATIONS — never by
rewriting the table — and the test that matters is not "the DDL parses" but
"an existing row still has its values afterwards".
"""
import sqlite3

import pytest

from backend.app import db as db_module
from backend.app.sources import mailbox

# The candidate table exactly as the schema before listed_at / contact_phone /
# contact_email created it. Copied rather than derived from db.SCHEMA on
# purpose: a test that builds the "old" schema out of the new one cannot
# notice a column quietly going missing from the new one.
PREVIOUS_SCHEMA = """
CREATE TABLE candidate (
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
"""

NEW_COLUMNS = ("listed_at", "contact_phone", "contact_email", "source_category")


@pytest.fixture
def old_database(tmp_path, monkeypatch):
    """A database created by the previous schema, holding one real row."""
    path = tmp_path / "old.db"
    cx = sqlite3.connect(path)
    cx.executescript(PREVIOUS_SCHEMA)
    cx.execute(
        "INSERT INTO candidate(ref,source,title,municipality,price_eur,plot_ares,"
        "notes,scores_json,archived) VALUES(?,?,?,?,?,?,?,?,?)",
        ("K008", "rinka", "Sodyba prienu r.", "Prienų rajono", 6000.0, 118.0,
         "sena pastaba", '{"power": 4}', 0))
    cx.commit()
    cx.close()
    monkeypatch.setattr(db_module, "DB_PATH", str(path))
    return path


def _columns() -> set[str]:
    with db_module.connect() as cx:
        return {r["name"] for r in cx.execute("PRAGMA table_info(candidate)")}


def test_the_previous_schema_really_lacks_the_new_columns(old_database):
    # Otherwise the migration test below would pass without migrating anything.
    assert not (_columns() & set(NEW_COLUMNS))


def test_migrating_an_old_database_adds_the_new_columns(old_database):
    db_module.init_db()
    assert set(NEW_COLUMNS) <= _columns()


def test_migrating_an_old_database_preserves_existing_rows(old_database):
    db_module.init_db()
    with db_module.connect() as cx:
        row = dict(cx.execute("SELECT * FROM candidate WHERE ref='K008'").fetchone())
    assert row["title"] == "Sodyba prienu r."
    assert row["municipality"] == "Prienų rajono"
    assert row["price_eur"] == 6000.0
    assert row["plot_ares"] == 118.0
    assert row["notes"] == "sena pastaba"
    assert row["scores_json"] == '{"power": 4}'
    # New columns read NULL for rows that predate them — "the page did not
    # carry one", not an empty string that would look like data.
    assert row["listed_at"] is None
    assert row["contact_phone"] is None
    assert row["contact_email"] is None
    # And never back-filled: this row was ingested before the poller read more
    # than one category, so it genuinely has no recorded category. Labelling it
    # 'sodybos' by inference would be a guess wearing the clothes of a fact.
    assert row["source_category"] is None


def test_migrating_an_old_database_creates_the_failure_ledger(old_database):
    """poll_failure arrives as a new table, not a new column.

    `CREATE TABLE IF NOT EXISTS` in SCHEMA is additive in exactly the way
    MIGRATIONS is — it runs on every boot and touches nothing that exists —
    but a table missing from a live database is a different failure from a
    missing column: every poll would raise instead of returning a null, so it
    is worth its own test on a database that predates it.
    """
    db_module.init_db()
    with db_module.connect() as cx:
        cols = {r["name"] for r in cx.execute("PRAGMA table_info(poll_failure)")}
    assert {"source", "category", "listing_id", "url", "failures", "reason",
            "given_up_at"} <= cols


def test_migrating_twice_is_a_no_op(old_database):
    db_module.init_db()
    db_module.init_db()
    with db_module.connect() as cx:
        assert cx.execute("SELECT COUNT(*) c FROM candidate").fetchone()["c"] == 1


def test_every_migration_names_a_column_the_schema_also_declares():
    """A column added by migration but missing from SCHEMA exists only on
    databases old enough to have been migrated — a fresh install would be
    missing it and every query naming it would fail."""
    for table, column, _ddl in db_module.MIGRATIONS:
        assert f"    {column} " in db_module.SCHEMA, f"{table}.{column} not in SCHEMA"


def test_every_migration_is_additive():
    for _table, _column, ddl in db_module.MIGRATIONS:
        assert ddl.upper().startswith("ALTER TABLE")
        assert "ADD COLUMN" in ddl.upper()
        assert "DROP" not in ddl.upper()


# --------------------------------------------------- mailbox._insert lockstep
# The INSERT names its columns and binds its parameters in two separate places,
# and nothing but counting keeps them aligned. Getting it wrong does not raise
# when the counts still match — it silently writes the phone into the notes.

def test_the_insert_column_list_and_placeholders_are_in_lockstep():
    sql = mailbox._INSERT_SQL
    columns = sql.split("candidate(", 1)[1].split(")", 1)[0].split(",")
    values = sql.split("VALUES(", 1)[1].rsplit(")", 1)[0].split(",")
    assert len(columns) == 28
    assert len(values) == 28
    assert values.count("?") == 24
    for name in ("listed_at", "contact_phone", "contact_email", "source_category"):
        assert name in [c.strip() for c in columns]


def test_insert_writes_the_new_fields_into_their_own_columns(old_database):
    """The lockstep check that a miscount cannot survive: put distinguishable
    values in and read them back out by name."""
    db_module.init_db()
    ref = mailbox._insert(
        {"source": "rinka", "url": "https://www.rinka.lt/skelbimas/x-id-1",
         "title": "Sodyba", "municipality": "Prienų rajono", "price_eur": 6000.0,
         "plot_ares": 118.0, "listed_at": "2021-07-05",
         "contact_phone": "+37067132403", "contact_email": "a@b.lt",
         "raw": "aprašymas"},
        hits=["p1"], fp="fp-lockstep")
    with db_module.connect() as cx:
        row = dict(cx.execute("SELECT * FROM candidate WHERE ref=?", (ref,)).fetchone())
    assert row["listed_at"] == "2021-07-05"
    assert row["contact_phone"] == "+37067132403"
    assert row["contact_email"] == "a@b.lt"
    assert row["notes"] == "aprašymas"          # nothing shifted by one column
    assert row["fingerprint"] == "fp-lockstep"
    assert row["match_state"] == "match"
    assert row["archived"] == 0
