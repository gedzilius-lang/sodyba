"""asking_vs_peers — one advert's asking price against the other adverts.

Not a valuation, and the tests are written to keep it that way: the sample
floor, the basis reporting and the self-exclusion are each asserted directly,
because each of them is the difference between a number worth reading and a
number that merely looks like one.

Every candidate in the table is a peer, so this module clears the candidate
table before each test the way test_poller.py already does — a stray row from
another module would move the medians and the assertions here are exact.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import api as api_module
from backend.app.db import connect, init_db

init_db()

app = FastAPI()
app.include_router(api_module.router)
client = TestClient(app)

ALYTUS = "Alytaus rajono"
VARENA = "Varėnos rajono"


@pytest.fixture(autouse=True)
def _empty_table():
    with connect() as cx:
        cx.execute("DELETE FROM candidate")
    yield
    with connect() as cx:
        cx.execute("DELETE FROM candidate")


def _insert(ref: str, price: float | None, plot: float | None = None,
            m2: float | None = None, muni: str = ALYTUS, archived: int = 0) -> None:
    with connect() as cx:
        cx.execute(
            "INSERT INTO candidate(ref,source,title,municipality,price_eur,"
            "house_m2,plot_ares,archived) VALUES(?,?,?,?,?,?,?,?)",
            (ref, "rinka", f"Sodyba {ref}", muni, price, m2, plot, archived))


def _items() -> dict[str, dict]:
    r = client.get("/api/candidates")
    assert r.status_code == 200
    return {c["ref"]: c for c in r.json()["items"]}


def _peers(ref: str, metric: str = "eur_per_are") -> dict:
    return _items()[ref]["asking_vs_peers"][metric]


def _five_peers_in(muni: str = ALYTUS) -> None:
    """Five candidates at 1, 2, 3, 4 and 5 EUR per are — median 3."""
    for i, price in enumerate((100.0, 200.0, 300.0, 400.0, 500.0), start=1):
        _insert(f"PP10{i}", price, plot=100.0, muni=muni)


# ------------------------------------------------------------ the basic ratio
def test_per_unit_values_are_computed_from_price_and_size():
    _insert("PP001", 20000.0, plot=50.0, m2=80.0)
    c = _items()["PP001"]
    assert c["eur_per_are"] == 400.0
    assert c["eur_per_m2"] == 250.0


def test_per_unit_values_are_none_when_a_size_is_missing_or_zero():
    _insert("PP002", 20000.0, plot=None, m2=0.0)
    c = _items()["PP002"]
    assert c["eur_per_are"] is None
    assert c["eur_per_m2"] is None


def test_per_unit_values_are_none_when_the_price_is_missing():
    _insert("PP003", None, plot=50.0)
    assert _items()["PP003"]["eur_per_are"] is None


# ------------------------------------------------------------- the sample floor
def test_no_ratio_is_computed_below_the_sample_floor():
    """Four peers is not a market. The answer is a null ratio and a reason,
    never a number nobody should trust."""
    for i in range(4):
        _insert(f"PP20{i}", 100.0 * (i + 1), plot=100.0)
    _insert("PP299", 100000.0, plot=100.0)

    p = _peers("PP299")
    assert p["ratio"] is None
    assert p["median"] is None
    assert p["basis"] is None
    assert p["n"] == 4
    assert "per mažai" in p["reason"]
    assert p["value"] == 1000.0            # its own metric is still reported


def test_a_ratio_appears_as_soon_as_the_floor_is_reached():
    _five_peers_in()
    _insert("PP299", 100000.0, plot=100.0, muni=VARENA)
    p = _peers("PP299")
    assert p["n"] == 5
    assert p["median"] == 3.0
    assert p["ratio"] == round(1000.0 / 3.0, 2)
    assert p["reason"] is None


def test_a_missing_metric_reports_a_reason_rather_than_a_ratio():
    _five_peers_in()
    _insert("PP299", 100000.0, plot=None)
    p = _peers("PP299")
    assert p["value"] is None
    assert p["ratio"] is None
    assert p["reason"] == "trūksta kainos arba dydžio"


# ------------------------------------------------------------------- the basis
def test_the_all_candidates_basis_is_used_when_a_municipality_is_too_thin():
    """Five peers, but all of them in a different municipality: the answer
    falls back to every candidate and says so."""
    _five_peers_in(ALYTUS)
    _insert("PP299", 100000.0, plot=100.0, muni=VARENA)
    p = _peers("PP299")
    assert p["basis"] == "all"
    assert p["n"] == 5


def test_the_municipality_basis_is_preferred_when_it_has_enough_peers():
    _five_peers_in(ALYTUS)
    _insert("PP299", 100000.0, plot=100.0, muni=ALYTUS)
    # Peers exist elsewhere too, so "municipality" is a real choice, not the
    # only option available.
    _insert("PP301", 9999.0, plot=1.0, muni=VARENA)
    p = _peers("PP299")
    assert p["basis"] == "municipality"
    assert p["n"] == 5
    assert p["median"] == 3.0              # the Varėna outlier is not in it


def test_nothing_is_computed_when_neither_basis_reaches_the_floor():
    _insert("PP401", 100.0, plot=100.0, muni=ALYTUS)
    _insert("PP402", 200.0, plot=100.0, muni=VARENA)
    p = _peers("PP401")
    assert p["basis"] is None
    assert p["ratio"] is None
    assert p["n"] == 1


# ------------------------------------------------------- who counts as a peer
def test_a_candidate_is_excluded_from_its_own_median():
    """Self-inclusion drags every ratio towards 1, and hardest for exactly the
    outliers this feature exists to surface. With peers at 1,2,3,4,5 the median
    is 3; including this candidate's own 1000 would make it 3.5."""
    _five_peers_in()
    _insert("PP299", 100000.0, plot=100.0, muni=VARENA)
    p = _peers("PP299")
    assert p["median"] == 3.0
    assert p["median"] != 3.5
    assert p["n"] == 5                     # five others, not six rows


def test_archived_rows_are_not_peers():
    """An archived row is a rejected property. Its asking price is not part of
    the comparison — and it must not silently change everyone else's median."""
    _five_peers_in()
    _insert("PP299", 600.0, plot=100.0, muni=VARENA)
    before = _peers("PP299")

    _insert("PP500", 100000.0, plot=100.0, muni=ALYTUS, archived=1)
    _insert("PP501", 100000.0, plot=100.0, muni=ALYTUS, archived=1)
    after = _peers("PP299")

    assert after["median"] == before["median"] == 3.0
    assert after["n"] == before["n"] == 5


def test_rows_without_the_metric_are_not_counted_in_the_sample_size():
    _five_peers_in()
    for i in range(3):
        _insert(f"PP60{i}", 10000.0, plot=None)   # no plot: no eur_per_are
    _insert("PP299", 600.0, plot=100.0, muni=VARENA)
    assert _peers("PP299")["n"] == 5


def test_the_medians_do_not_move_when_the_view_is_filtered():
    """The peer pool is read separately from the filtered listing query on
    purpose: a candidate must not compare differently depending on which
    filter the user happens to have open."""
    _five_peers_in()
    _insert("PP299", 600.0, plot=100.0, muni=VARENA)

    unfiltered = _peers("PP299")
    r = client.get("/api/candidates?min_price=550")
    filtered = next(c for c in r.json()["items"]
                    if c["ref"] == "PP299")["asking_vs_peers"]["eur_per_are"]
    assert filtered["median"] == unfiltered["median"] == 3.0
    assert filtered["n"] == unfiltered["n"] == 5


# ------------------------------------------------------ what it must not claim
def test_the_response_never_calls_this_a_market_value():
    _five_peers_in()
    body = client.get("/api/candidates").json()
    assert "asking_vs_peers_note" in body
    for c in body["items"]:
        assert "asking_vs_peers" in c
        assert "market_value" not in c
        assert "rinkos_verte" not in c


def test_the_note_says_plainly_that_these_are_asking_prices():
    body = client.get("/api/candidates").json()
    note = body["asking_vs_peers_note"].lower()
    assert "prašomomis kainomis" in note
    assert "ne rinkos vertė" in note
    assert "sandorio kaina" in note
