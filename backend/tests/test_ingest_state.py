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
