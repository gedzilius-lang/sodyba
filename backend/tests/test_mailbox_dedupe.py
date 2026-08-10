"""_insert's duplicate-merge path: a cross-portal duplicate must not create a
second candidate row, and the merge itself must be visible rather than silent.

Every listing below is spelled out field by field. `{**first, ...}` is the
idiom that let two earlier bugs through here — it copies the very fields the
merge decision turns on, so a test written that way asserts far less than it
looks like it does.
"""
import json

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


def test_a_match_promotes_a_stored_near_miss_and_returns_its_ref():
    """Dedupe tolerates 5% on price; the near-miss band is 25%. A listing that
    genuinely satisfies the profile therefore lands on a stored near-miss of
    the same property routinely — and being absorbed into it made the property
    invisible in every default view and pushed no notification."""
    init_db()
    near = {"source": "rinka", "url": "https://www.rinka.lt/skelbimas/promo-1",
            "municipality": "Zarasų rajono", "locality": "Salako k.",
            "price_eur": 20500, "house_m2": 80, "plot_ares": 40,
            "title": "Sodyba prie ežero su pirtimi", "cadastral_no": None}
    ref1 = _insert(near, ["lake_shore"], _fingerprint(near), "near",
                   {"lake_shore": [{"field": "price", "kind": "soft",
                                    "text": "kaina 20 500 > 20 000 EUR",
                                    "delta": 500}]})
    assert ref1 is not None

    match = {"source": "aruodas", "url": "https://www.aruodas.lt/skelbimas/promo-2",
             "municipality": "Zarasų rajono", "locality": "Salako k.",
             "price_eur": 19900, "house_m2": 80, "plot_ares": 40,
             "title": "Sodyba prie ežero su pirtimi", "cadastral_no": None}
    ref2 = _insert(match, ["auction_hunt"], _fingerprint(match), "match", {})

    assert ref2 == ref1, "the promoted twin's ref is what reaches notify.push"

    with connect() as cx:
        rows = cx.execute("SELECT * FROM candidate WHERE url IN (?,?)",
                          (near["url"], match["url"])).fetchall()
    assert len(rows) == 1, "promotion must not create a second row"
    row = rows[0]
    assert row["match_state"] == "match"
    assert row["price_eur"] == 19900, \
        "a promoted row must show the price that earned the match"
    assert json.loads(row["costs_json"])["purchase"] == 19900, \
        "the cost model must not keep costing the near-miss price"
    assert json.loads(row["misses_json"]) == {}, \
        "a match has no misses left to explain"
    assert set(json.loads(row["profiles_json"])) == {"lake_shore", "auction_hunt"}


def test_the_duplicate_note_survives_a_promotion():
    """The evidence line is how a wrong merge is spotted afterwards. It must
    not be dropped just because the merge also promoted the row."""
    init_db()
    near = {"source": "rinka", "url": "https://www.rinka.lt/skelbimas/promonote-1",
            "municipality": "Švenčionių rajono", "locality": "Kaltanėnų k.",
            "price_eur": 21000, "house_m2": 70, "plot_ares": 35,
            "title": "Sodyba su tvartu ir sodu", "cadastral_no": None}
    ref1 = _insert(near, ["lake_shore"], _fingerprint(near), "near",
                   {"lake_shore": [{"field": "price", "kind": "soft",
                                    "text": "kaina 21 000 > 20 000 EUR",
                                    "delta": 1000}]})
    assert ref1 is not None

    match = {"source": "domoplius",
             "url": "https://www.domoplius.lt/skelbimas/promonote-2",
             "municipality": "Švenčionių rajono", "locality": "Kaltanėnų k.",
             "price_eur": 20100, "house_m2": 70, "plot_ares": 35,
             "title": "Sodyba su tvartu ir sodu", "cadastral_no": None}
    ref2 = _insert(match, ["lake_shore"], _fingerprint(match), "match", {})
    assert ref2 == ref1

    with connect() as cx:
        row = cx.execute("SELECT notes FROM candidate WHERE url=?",
                         (near["url"],)).fetchone()
    notes = row["notes"] or ""
    assert "[dublikatas" in notes
    assert match["url"] in notes
    assert "20100" in notes


def test_a_near_miss_arriving_after_a_match_does_not_demote_it():
    """The promotion is one-way. A near miss carries less information than the
    match already stored, so it merges away exactly as before."""
    init_db()
    match = {"source": "rinka", "url": "https://www.rinka.lt/skelbimas/nodemote-1",
             "municipality": "Ignalinos rajono", "locality": "Mielagėnų k.",
             "price_eur": 15000, "house_m2": 90, "plot_ares": 50,
             "title": "Sodyba prie upelio, rąstinė", "cadastral_no": None}
    ref1 = _insert(match, ["lake_shore"], _fingerprint(match), "match", {})
    assert ref1 is not None

    near = {"source": "aruodas", "url": "https://www.aruodas.lt/skelbimas/nodemote-2",
            "municipality": "Ignalinos rajono", "locality": "Mielagėnų k.",
            "price_eur": 15400, "house_m2": 90, "plot_ares": 50,
            "title": "Sodyba prie upelio, rąstinė", "cadastral_no": None}
    ref2 = _insert(near, ["lake_shore"], _fingerprint(near), "near",
                   {"lake_shore": [{"field": "plot_ares", "kind": "soft",
                                    "text": "sklypas 50 a < 60 a", "delta": 10}]})
    assert ref2 is None, "a near miss must not be notified as a new candidate"

    with connect() as cx:
        rows = cx.execute("SELECT * FROM candidate WHERE url IN (?,?)",
                          (match["url"], near["url"])).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["match_state"] == "match", "a stored match must never be demoted"
    assert row["price_eur"] == 15000
    assert json.loads(row["misses_json"]) == {}, \
        "the near miss's misses must not be written over a match"


def test_two_near_misses_merge_into_one_near_row():
    """Unchanged behaviour: same state on both sides, second row discarded."""
    init_db()
    first = {"source": "rinka", "url": "https://www.rinka.lt/skelbimas/twonear-1",
             "municipality": "Molėtų rajono", "locality": "Suginčių k.",
             "price_eur": 24000, "house_m2": 65, "plot_ares": 28,
             "title": "Sodyba ant kalvos, mūrinė", "cadastral_no": None}
    ref1 = _insert(first, ["lake_shore"], _fingerprint(first), "near",
                   {"lake_shore": [{"field": "price", "kind": "soft",
                                    "text": "kaina 24 000 > 20 000 EUR",
                                    "delta": 4000}]})
    assert ref1 is not None

    second = {"source": "skelbiu", "url": "https://www.skelbiu.lt/skelbimas/twonear-2",
              "municipality": "Molėtų rajono", "locality": "Suginčių k.",
              "price_eur": 23500, "house_m2": 65, "plot_ares": 28,
              "title": "Sodyba ant kalvos, mūrinė", "cadastral_no": None}
    ref2 = _insert(second, ["lake_shore"], _fingerprint(second), "near",
                   {"lake_shore": [{"field": "price", "kind": "soft",
                                    "text": "kaina 23 500 > 20 000 EUR",
                                    "delta": 3500}]})
    assert ref2 is None

    with connect() as cx:
        rows = cx.execute("SELECT * FROM candidate WHERE url IN (?,?)",
                          (first["url"], second["url"])).fetchall()
    assert len(rows) == 1
    assert rows[0]["match_state"] == "near"
    assert rows[0]["price_eur"] == 24000


def test_a_live_listing_is_not_absorbed_into_an_archived_candidate():
    """Archiving is the user rejecting a listing. If the same property then
    arrives from another portal and merges into the archived row, the property
    is invisible in every default view — rejecting one portal's version of it
    would permanently delete the property."""
    init_db()
    first = {"source": "rinka", "url": "https://www.rinka.lt/skelbimas/arch-1",
             "municipality": "Anykščių rajono", "locality": "Debeikių k.",
             "price_eur": 15000, "house_m2": 72, "plot_ares": 33,
             "title": "Sodyba su tvenkiniu ir rūsiu", "cadastral_no": None}
    ref1 = _insert(first, ["p1"], _fingerprint(first), "match", {})
    assert ref1 is not None
    with connect() as cx:
        cx.execute("UPDATE candidate SET archived=1 WHERE url=?", (first["url"],))

    second = {"source": "aruodas", "url": "https://www.aruodas.lt/skelbimas/arch-2",
              "municipality": "Anykščių rajono", "locality": "Debeikių k.",
              "price_eur": 15200, "house_m2": 72, "plot_ares": 33,
              "title": "Sodyba su tvenkiniu ir rūsiu", "cadastral_no": None}
    ref2 = _insert(second, ["p1"], _fingerprint(second), "match", {})
    assert ref2 is not None, "an archived twin must not swallow a live listing"

    with connect() as cx:
        rows = cx.execute(
            "SELECT url,archived FROM candidate WHERE url IN (?,?) ORDER BY url",
            (first["url"], second["url"])).fetchall()
    assert len(rows) == 2, "the live listing needs a row of its own"
    live = [r for r in rows if r["url"] == second["url"]]
    assert live and live[0]["archived"] == 0
