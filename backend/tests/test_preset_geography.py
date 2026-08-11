"""The four municipality lists behind PRESETS (filters.py) widened when a
poller was added alongside mailbox ingestion: the mailbox path only ever saw
listings a portal's own saved search had already narrowed by geography, so
the lists could stay narrow too. The poller brings the raw nationwide feed,
so the lists had to widen — each on its own logic (HIGH_UTILITY is measured
and must not drift, the other three are geographic and were widened by
region). These tests guard the two ways that widening can go wrong: a typo
that silently narrows a profile instead of widening it, and a profile that
stops being geographically distinct from the others.
"""
from backend.app import filters as f
from backend.app.config import ALL_MUNICIPALITIES

ALL = set(ALL_MUNICIPALITIES)

LISTS = {
    "HIGH_UTILITY": f.HIGH_UTILITY,
    "LAKE_BELT": f.LAKE_BELT,
    "FOREST_BELT": f.FOREST_BELT,
    "CHEAPEST": f.CHEAPEST,
}


def test_every_municipality_in_every_list_is_a_real_municipality():
    for name, munis in LISTS.items():
        unknown = [m for m in munis if m not in ALL]
        assert not unknown, f"{name} has unknown municipalities: {unknown}"


def test_every_municipality_in_every_preset_is_a_real_municipality():
    for preset in f.PRESETS:
        unknown = [m for m in preset["municipalities"] if m not in ALL]
        assert not unknown, (
            f"preset {preset['key']!r} has unknown municipalities: {unknown}")


def test_high_utility_and_cheapest_do_not_overlap():
    # They encode opposite theses: HIGH_UTILITY is where infrastructure is
    # measurably common, CHEAPEST is where the fund is cheap regardless.
    overlap = set(f.HIGH_UTILITY) & set(f.CHEAPEST)
    assert not overlap, f"HIGH_UTILITY and CHEAPEST both contain: {overlap}"


def test_no_list_contains_duplicates():
    for name, munis in LISTS.items():
        assert len(munis) == len(set(munis)), f"{name} has duplicate entries"


def test_high_utility_is_pinned_to_the_measured_rank_order():
    # This is the one list derived from real data (NTR building register:
    # share with registered power+water x share built pre-1945). The cut is
    # the observed break in the ranking (4.83 down to 3.85), not a round
    # number, and Šalčininkų is deliberately excluded despite being
    # geographically close to several of these. Pinned so a future edit
    # cannot turn "measured" back into "chosen" without this test noticing.
    assert f.HIGH_UTILITY == [
        "Ukmergės rajono", "Utenos rajono", "Anykščių rajono", "Zarasų rajono",
        "Molėtų rajono", "Ignalinos rajono", "Širvintų rajono", "Varėnos rajono",
        "Prienų rajono", "Rokiškio rajono", "Švenčionių rajono", "Trakų rajono",
    ]


def test_the_four_profiles_remain_geographically_distinct():
    # Widening must not collapse the four lists into one undifferentiated
    # "anywhere in Lithuania" set — that would defeat the point of having
    # separate profiles at all.
    as_sets = [frozenset(munis) for munis in LISTS.values()]
    assert len(set(as_sets)) > 1


# --------------------------------------------------------- radius geography
# `zemaitija_lakes` is the first preset to locate itself with centres and a
# radius instead of a municipality list, so these guard the second way of
# writing geography rather than the first.

def test_every_preset_carries_the_full_field_set():
    # Presets are read straight out of code by api.profiles() when nothing has
    # been saved yet, so a preset missing a key is not merely untidy: every
    # reader would fall through to .get(...) -> None and the gate it names
    # would quietly stop existing. Sixth preset added by hand; pin the shape.
    expected = set(f.FIELDS)
    for preset in f.PRESETS:
        assert set(preset) == expected, (
            f"preset {preset['key']!r} field set differs: "
            f"missing {sorted(expected - set(preset))}, "
            f"extra {sorted(set(preset) - expected)}")


def test_preset_keys_are_unique():
    keys = [p["key"] for p in f.PRESETS]
    assert len(keys) == len(set(keys))


def test_a_preset_with_centres_also_sets_a_radius():
    # filters._radius_misses returns nothing at all when either half is
    # missing, so centres without a radius is a geography that silently does
    # not apply — exactly the failure this suite exists to catch.
    for preset in f.PRESETS:
        if preset["centres"]:
            assert preset["radius_km"], (
                f"preset {preset['key']!r} lists centres but no radius_km")


def test_the_zemaitija_preset_locates_by_radius_alone():
    p = next(x for x in f.PRESETS if x["key"] == "zemaitija_lakes")
    assert p["centres"] == f.ZEMAITIJA_LAKES
    assert p["municipalities"] == [], (
        "the interest is the lakes and the country round them, not a district")
    assert p["enabled"] is True


def test_the_zemaitija_radius_closes_the_widest_gap_in_the_chain():
    # The five anchors form a chain and a listing is measured to the NEAREST
    # centre, so the circles union into a corridor only if the radius covers
    # half the widest adjacent gap: Lūkstas to Rietavas is 25 km, so anything
    # under 12.5 leaves a hole in the middle of the search area.
    p = next(x for x in f.PRESETS if x["key"] == "zemaitija_lakes")
    assert p["radius_km"] >= 12.5


def test_the_zemaitija_preset_gates_on_place_not_on_specification():
    # Permissive on what, strict on where. A water gate in particular would
    # hard-miss every row whose nature has not been measured yet.
    p = next(x for x in f.PRESETS if x["key"] == "zemaitija_lakes")
    assert p["max_lake_m"] is None and p["max_river_m"] is None
    assert p["min_lake_ha"] is None
    assert p["require_any"] == [] and p["require_all"] == []
    assert p["min_plot_ares"] == 0
    assert 3000 <= p["min_price"] and p["max_price"] <= 25000
