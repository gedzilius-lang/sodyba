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


def test_different_villages_are_not_merged_through_the_insert_path():
    """The locality gate must work on rows fetched from the database, not
    only on dicts built by hand. Every prior test copied locality from one
    listing to the other, so the gate was never actually exercised."""
    init_db()
    a = {"source": "rinka", "url": "https://www.rinka.lt/skelbimas/vil-1",
         "municipality": "Utenos rajono", "locality": "Kirdeikių k.",
         "price_eur": 17000, "house_m2": 80, "plot_ares": 40,
         "title": "Sodyba su sodu ir garažu", "cadastral_no": None}
    ref1 = _insert(a, ["p1"], _fingerprint(a), "match", {})
    assert ref1 is not None

    b = {"source": "aruodas", "url": "https://www.aruodas.lt/skelbimas/vil-2",
         "municipality": "Utenos rajono", "locality": "Sudeikių k.",
         "price_eur": 17300, "house_m2": 81, "plot_ares": 41,
         "title": "Sodyba su sodu ir garažu", "cadastral_no": None}
    ref2 = _insert(b, ["p1"], _fingerprint(b), "match", {})
    assert ref2 is not None, "different villages must not be merged"

    with connect() as cx:
        matches = cx.execute(
            "SELECT * FROM candidate WHERE url IN (?,?)", (a["url"], b["url"])
        ).fetchall()
    assert len(matches) == 2, "each village's listing must keep its own row"


def test_unusable_titles_still_merge_on_a_matching_locality_through_insert():
    """Companion to the villages test: the round-1 fallback (merge on a
    genuinely matching locality when neither title has usable words) must
    still fire once locality reaches find_duplicate through the database,
    not only through hand-built dicts."""
    init_db()
    a = {"source": "rinka", "url": "https://www.rinka.lt/skelbimas/blank-1",
         "municipality": "Utenos rajono", "locality": "Antalgės k.",
         "price_eur": 12000, "house_m2": 60, "plot_ares": 25,
         "title": "Parduodama sodyba", "cadastral_no": None}
    ref1 = _insert(a, ["p1"], _fingerprint(a), "match", {})
    assert ref1 is not None

    b = {"source": "aruodas", "url": "https://www.aruodas.lt/skelbimas/blank-2",
         "municipality": "Utenos rajono", "locality": "Antalgės k.",
         "price_eur": 12200, "house_m2": 61, "plot_ares": 26,
         "title": "Parduodama sodyba", "cadastral_no": None}
    ref2 = _insert(b, ["p1"], _fingerprint(b), "match", {})
    assert ref2 is None, "a genuinely matching locality must still merge"

    with connect() as cx:
        matches = cx.execute(
            "SELECT * FROM candidate WHERE url IN (?,?)", (a["url"], b["url"])
        ).fetchall()
    assert len(matches) == 1, "matching-locality merge must land on one row"
