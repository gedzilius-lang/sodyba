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
    #
    # The locality differs from the test above on purpose: with the same one,
    # dedupe folds this listing into that row, _insert returns None, and the
    # assertion below passes without a row ever being written. A test that
    # cannot fail is itself a confidently wrong claim of coverage.
    li = _listing(url="https://www.rinka.lt/skelbimas/cat-b-id-90002",
                  locality="Bekategorė")
    ref = _insert(li, ["p"], fingerprint(li))
    assert ref is not None
    assert _column(ref, "source_category") is None


def test_the_insert_column_and_placeholder_counts_agree():
    # The INSERT is one string constant; a column added without its placeholder
    # fails only at runtime, on a real ingest.
    from backend.app.sources import mailbox
    sql = getattr(mailbox, "INSERT", None) or getattr(mailbox, "_INSERT_SQL", None)
    assert sql, "name the INSERT constant so it can be asserted on"
    cols = sql.split("INSERT INTO candidate(")[1].split(")")[0]
    values = sql.split("VALUES(")[1].rsplit(")", 1)[0]
    assert len(cols.split(",")) == len(values.split(","))
