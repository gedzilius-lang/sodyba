"""Profile centres resolve against places *and* water, and never in silence.

`filters._radius_misses` used to resolve every centre with `geocode`, which
reads the settlement gazetteer only, and `continue` past the ones that came
back empty. A profile naming five centres of which four were settlements and
one was a lake therefore searched four circles, returned a smaller answer than
it was asked for, and said nothing about it — the same shape as the locality
gate that sat dead for three review rounds because nothing surfaced its
silence.

Two things are pinned here: `resolve_centre` finds the lake that `geocode`
cannot, without letting `geocode` itself start matching water (a *listing's*
locality is a settlement, and a homestead placed in the middle of a lake would
measure every distance it owns from there); and an unresolved centre becomes a
hard miss rather than a quietly narrower search.

The rows below are the real Žemaitija ones — the same coordinates and areas the
production database holds — so the test exercises the actual names in
ZEMAITIJA_LAKES rather than invented ones. conftest.py points SR_DATA_DIR at a
throwaway directory shared by the whole run, so the fixture deletes everything
it inserted; other modules rely on `geocode` finding nothing.
"""
import pytest

from backend.app import filters as f
from backend.app.db import connect, init_db
from backend.app.sources.nature import cell_of, geocode, resolve_centre

init_db()

# name, municipality, easting, northing, area_ha
PLACES = [
    (9_100_001, "Rietavo m.", "Rietavo", 369654.29, 6179051.05, 477.5),
    (9_100_002, "Plungės m.", "Plungės rajono", 365354.82, 6199535.94, 1185.0),
    (9_100_003, "Platelių k.", "Plungės rajono", 363855.46, 6214056.38, 727.4),
    (9_100_004, "Platelių mstl.", "Plungės rajono", 363925.21, 6214444.57, 183.2),
]

# kind, name, easting, northing, size (lakes in ha, rivers in km)
WATER = [
    ("lake", "Platelių ežeras", 366547.0, 6213827.0, 1181.5),
    ("lake", "Lūkstas", 394683.0, 6175432.0, 1000.9),
    ("river", "Lūkstas", 435941.0, 6213546.0, 11.583),
    ("lake", "Luksnėnų ežeras", 494818.0, 6027987.0, 62.2),
    ("river", "Minija", 340000.0, 6190000.0, 202.5),
]


@pytest.fixture(scope="module", autouse=True)
def _gazetteer():
    with connect() as cx:
        for code, name, muni, e, n, ha in PLACES:
            cx.execute(
                "INSERT OR REPLACE INTO place(code,name,municipality,easting,"
                "northing,area_ha,cell_x,cell_y) VALUES(?,?,?,?,?,?,?,?)",
                (code, name, muni, e, n, ha, *cell_of(e, n)))
        for kind, name, e, n, size in WATER:
            cx.execute(
                "INSERT INTO water_feature(kind,name,easting,northing,size,"
                "cell_x,cell_y) VALUES(?,?,?,?,?,?,?)",
                (kind, name, e, n, size, *cell_of(e, n)))
    yield
    with connect() as cx:
        cx.execute("DELETE FROM place WHERE code BETWEEN 9100000 AND 9199999")
        cx.execute("DELETE FROM water_feature WHERE name IN (%s)"
                   % ",".join("?" * len(WATER)), [w[1] for w in WATER])


# ------------------------------------------------------------- resolve_centre
def test_a_settlement_resolves_as_a_place():
    r = resolve_centre("Rietavas")
    assert r["name"] == "Rietavo m."
    assert r["kind"] == "place"
    assert round(r["easting"]) == 369654


def test_a_lake_with_no_settlement_of_its_own_resolves_to_the_lake():
    # The whole point: "Lūkstas" is 1001 ha of water and nothing else. geocode
    # returns None for it, which is why the profile searched one circle fewer.
    r = resolve_centre("Lūkstas")
    assert r is not None, "a lake named as a centre must resolve"
    assert r["kind"] == "lake"
    assert r["name"] == "Lūkstas"
    assert round(r["size"]) == 1001, "must be the big lake, not a same-stem pond"
    assert round(r["easting"]) == 394683


def test_the_lake_beats_the_river_that_shares_its_name():
    # `water_feature` holds a river called Lūkstas too, 40 km away.
    assert round(resolve_centre("Lūkstas")["northing"]) == 6175432


def test_a_settlement_wins_over_the_lake_beside_it():
    # Plateliai is a village and Platelių ežeras is its lake; the operator
    # naming Plateliai means the village, which is where the listings are.
    r = resolve_centre("Plateliai")
    assert r["kind"] == "place"
    assert r["name"] == "Platelių k."


def test_the_lake_can_still_be_named_in_full():
    r = resolve_centre("Platelių ežeras")
    assert r["kind"] == "lake"
    assert round(r["size"]) == 1182


def test_geocode_itself_never_matches_water():
    # Deliberately unchanged. A listing's locality is a settlement; if geocode
    # fell back to water, a homestead would be placed mid-lake and every
    # distance measured from there.
    assert geocode("Lūkstas") is None
    assert geocode("Platelių ežeras") is None


def test_a_river_is_not_a_centre():
    # A river is one point off a line up to 476 km long — those coordinates are
    # not a place. Unresolved (and therefore loud) beats a circle drawn
    # somewhere nobody asked for.
    assert resolve_centre("Minija") is None


def test_an_unknown_name_resolves_to_nothing():
    assert resolve_centre("Nesamaviete") is None
    assert resolve_centre("") is None
    assert resolve_centre(None) is None


# ------------------------------------------------------------ _radius_misses
PROFILE = {
    "key": "t", "name": "Testas", "enabled": True,
    "min_price": None, "max_price": None,
    "min_plot_ares": None, "min_house_m2": None,
    "municipalities": [], "require_any": [], "require_all": [],
    "exclude_any": [], "sources": [], "centres": [], "radius_km": 15,
    "max_lake_m": None, "max_river_m": None, "min_lake_ha": None,
}
# 5 km north of Rietavas, ~44 km from Lūkstas.
NEAR_RIETAVAS = {"title": "Sodyba", "source": "rinka",
                 "easting": 369654.29, "northing": 6184051.05}


def test_a_listing_inside_the_radius_is_clean():
    p = {**PROFILE, "centres": ["Rietavas", "Plungė", "Lūkstas"]}
    assert f.evaluate(NEAR_RIETAVAS, p).misses == []


def test_over_radius_is_still_a_soft_miss_carrying_its_delta():
    # Unchanged on purpose: this is what lets a row just outside reach Beveik.
    # The listing sits 16.1 km from Plungė, so a 14 km radius misses it by 2.1.
    p = {**PROFILE, "centres": ["Plungė"], "radius_km": 14}
    m = next(x for x in f.evaluate(NEAR_RIETAVAS, p).misses if x.field == "radius_km")
    assert m.kind == f.SOFT
    assert m.delta == pytest.approx(2.07, abs=0.05)
    assert f.evaluate(NEAR_RIETAVAS, p).state == f.NEAR


def test_one_unresolvable_centre_is_a_hard_miss_not_a_quieter_search():
    p = {**PROFILE, "centres": ["Rietavas", "Nesamaviete"]}
    r = f.evaluate(NEAR_RIETAVAS, p)
    m = next(x for x in r.misses if x.field == "radius_km")
    assert m.kind == f.HARD
    assert "Nesamaviete" in m.text
    assert r.state == f.REJECT


def test_the_unresolvable_centre_is_named_so_it_can_be_fixed():
    p = {**PROFILE, "centres": ["Rietavas", "Nesamaviete", "Nieksdarnieko"]}
    m = next(x for x in f.evaluate(NEAR_RIETAVAS, p).misses if x.field == "radius_km")
    assert "Nesamaviete" in m.text and "Nieksdarnieko" in m.text


def test_no_centre_resolving_keeps_its_own_message():
    # Existing behaviour, kept: this reads differently from "one of five is
    # wrong" and a fresh install hits it on every listing until the gazetteer
    # finishes downloading.
    p = {**PROFILE, "centres": ["Nesamaviete"]}
    m = next(x for x in f.evaluate(NEAR_RIETAVAS, p).misses if x.field == "radius_km")
    assert m.kind == f.HARD
    assert m.text == "nė vienas profilio centras neatpažintas"


def test_every_centre_of_the_shipped_zemaitija_preset_resolves():
    # The preset is the first one ever to set `centres`, so nothing else in
    # the suite would notice a name the gazetteer cannot place.
    unresolved = [c for c in f.ZEMAITIJA_LAKES if resolve_centre(c) is None]
    assert not unresolved, f"unresolvable centres in the preset: {unresolved}"


def test_the_resolve_route_reports_good_and_bad_centres_in_one_answer():
    # The profile editor reads this back beside the centres box. A wrong
    # resolution — geocode("Varniai") lands 150 km away in Radviliškio rajono —
    # looks exactly like a right one until somebody sees the name it chose, so
    # the route reports null instead of 404 and never refuses anything.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.app import api as api_module

    app = FastAPI()
    app.include_router(api_module.router)
    r = TestClient(app).post("/api/centres/resolve",
                             json={"centres": ["Rietavas", "Lūkstas", "Nesamaviete"]})
    assert r.status_code == 200
    got = {c["centre"]: c["resolved"] for c in r.json()["centres"]}
    assert got["Rietavas"]["name"] == "Rietavo m."
    assert got["Rietavas"]["kind"] == "place"
    assert got["Lūkstas"]["kind"] == "lake"
    assert round(got["Lūkstas"]["size_ha"]) == 1001
    assert got["Nesamaviete"] is None


def test_the_zemaitija_centres_resolve_to_the_intended_places():
    got = {c: resolve_centre(c)["name"] for c in f.ZEMAITIJA_LAKES}
    assert got == {
        "Rietavas": "Rietavo m.",
        "Plungė": "Plungės m.",
        "Plateliai": "Platelių k.",
        "Platelių ežeras": "Platelių ežeras",
        "Lūkstas": "Lūkstas",
    }
