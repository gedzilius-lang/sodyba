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


def test_the_surviving_row_keeps_the_category_it_was_stored_under():
    # A merge must not silently relabel provenance: the row still came from
    # the category that actually produced it, and the loser's category is
    # visible in the notes trail rather than overwritten on top of the winner.
    loc = "Kategorija"
    a = _twin("https://www.rinka.lt/skelbimas/x-id-95007", "sodybos", locality=loc)
    _insert(a, ["p"], fingerprint(a))
    b = _twin("https://www.rinka.lt/skelbimas/y-id-95008", "namai", locality=loc)
    _insert(b, ["p"], fingerprint(b))
    with connect() as cx:
        row = cx.execute("SELECT source_category FROM candidate WHERE locality=?",
                         (loc,)).fetchone()
    assert row["source_category"] == "sodybos"


def test_two_genuinely_different_properties_are_not_merged():
    a = _twin("https://www.rinka.lt/skelbimas/x-id-95005", "sodybos",
              locality="SkirtingaĀ", price_eur=12000.0)
    _insert(a, ["p"], fingerprint(a))
    b = _twin("https://www.rinka.lt/skelbimas/y-id-95006", "namai",
              locality="SkirtingaĀ", price_eur=24000.0, plot_ares=8.0)
    _insert(b, ["p"], fingerprint(b))
    assert _count("SkirtingaĀ") == 2
