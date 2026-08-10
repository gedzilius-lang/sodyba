"""_insert's duplicate-merge path: a cross-portal duplicate must not create a
second candidate row, and the merge itself must be visible rather than silent.
"""
from backend.app.db import connect, init_db
from backend.app.sources.mailbox import _insert, _fingerprint


def test_a_merged_duplicate_records_enough_to_spot_a_wrong_merge():
    """A merge must be visible: price, size and title of the discarded row
    land in the survivor's notes, so a wrong match can be seen and undone."""
    init_db()
    a = {"source": "rinka", "url": "https://www.rinka.lt/skelbimas/aaa-1",
         "municipality": "Utenos rajono", "locality": "Kirdeikių k.",
         "price_eur": 17000, "house_m2": 81, "plot_ares": 20,
         "title": "Parduodama sodyba Utenos r. prie ežero", "cadastral_no": None}
    ref1 = _insert(a, ["p1"], _fingerprint(a), "match", {})
    assert ref1 is not None

    b = {**a, "source": "aruodas", "url": "https://www.aruodas.lt/skelbimas/bbb-2",
         "price_eur": 16600, "house_m2": 80, "plot_ares": 19,
         "title": "Sodyba prie ežero, Utenos r., parduodama"}
    ref2 = _insert(b, ["p1"], _fingerprint(b), "match", {})
    assert ref2 is None

    with connect() as cx:
        # The test DB is session-scoped and shared with every other test file,
        # so scope the count to the two URLs this test itself inserted rather
        # than asserting on the whole table.
        matches = cx.execute(
            "SELECT * FROM candidate WHERE url IN (?,?)", (a["url"], b["url"])
        ).fetchall()
        no_second_row = cx.execute(
            "SELECT 1 FROM candidate WHERE url=?", (b["url"],)
        ).fetchone()

    assert len(matches) == 1, "the duplicate must not create a second row"
    assert no_second_row is None, "the discarded listing's own url must not be a row"
    notes = matches[0]["notes"] or ""
    assert "16600" in notes, "the discarded listing's price must be visible"
    assert b["title"] in notes, "the discarded listing's title must be visible"
    assert b["url"] in notes, "the discarded listing's url must be visible"
