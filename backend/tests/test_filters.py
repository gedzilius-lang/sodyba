import pytest

from backend.app import filters as f

PROFILE = {
    "key": "t", "name": "Testas", "enabled": True,
    "min_price": 3000, "max_price": 20000,
    "min_plot_ares": 30, "min_house_m2": 40,
    "municipalities": ["Utenos rajono"],
    "require_any": [], "require_all": [], "exclude_any": ["dalis", "butas"],
    "sources": [], "centres": [], "radius_km": None,
    "max_lake_m": None, "max_river_m": None, "min_lake_ha": None,
}

GOOD = {"title": "Sodyba prie miško", "municipality": "Utenos rajono",
        "price_eur": 15000, "plot_ares": 50, "house_m2": 60, "source": "rinka"}


def test_clean_listing_matches():
    r = f.evaluate(GOOD, PROFILE)
    assert r.state == f.MATCH
    assert r.misses == []


def test_every_miss_is_reported_not_just_the_first():
    bad = {**GOOD, "price_eur": 90000, "plot_ares": 5, "house_m2": 10}
    r = f.evaluate(bad, PROFILE)
    fields = {m.field for m in r.misses}
    assert fields == {"price", "plot_ares", "house_m2"}
    assert r.state == f.REJECT


def test_excluded_word_is_a_hard_miss():
    r = f.evaluate({**GOOD, "title": "Parduodama dalis sodybos"}, PROFILE)
    assert r.state == f.REJECT
    assert [m.kind for m in r.misses if m.field == "exclude_any"] == [f.HARD]


def test_delta_records_how_far_over():
    r = f.evaluate({**GOOD, "price_eur": 21000}, PROFILE)
    price = next(m for m in r.misses if m.field == "price")
    assert price.delta == 1000


def test_disabled_profile_never_matches():
    r = f.evaluate(GOOD, {**PROFILE, "enabled": False})
    assert r.state == f.REJECT


def test_match_all_wrapper_returns_matching_keys():
    assert f.match_all(GOOD, [PROFILE]) == ["t"]
    assert f.match_all({**GOOD, "price_eur": 90000}, [PROFILE]) == []


def test_matches_wrapper_keeps_v1_shape():
    ok, why = f.matches(GOOD, PROFILE)
    assert ok is True and why == ""
    ok, why = f.matches({**GOOD, "price_eur": 90000}, PROFILE)
    assert ok is False and "kaina" in why


NATURE = {**PROFILE, "max_lake_m": 300, "max_river_m": 500, "min_lake_ha": 5}


def _with(lake=None, river=None):
    # `located` is what advisor.assess_nature always sets on a measured
    # result; without it these fixtures described a shape the real code
    # never produces, and the location-unknown guard below could not tell
    # them apart from a listing nobody managed to place.
    return {**GOOD, "nature": {"located": True,
                               "nearest_lake": lake, "nearest_river": river}}


def test_missing_river_is_forgiven_when_the_lake_qualifies():
    # OR intent: the too-far branch already forgave a distant river when
    # the lake qualified; missing river data is now treated the same way.
    r = f.evaluate(_with(lake={"distance_m": 100, "size": 9}), NATURE)
    assert r.misses == []
    assert r.state == f.MATCH


def test_missing_river_still_misses_when_the_lake_does_not_qualify():
    r = f.evaluate(_with(lake={"distance_m": 5000, "size": 9}), NATURE)
    assert {m.field for m in r.misses} == {"max_lake_m", "max_river_m"}
    assert r.state == f.REJECT


def test_river_too_far_is_forgiven_when_lake_is_within_range():
    r = f.evaluate(_with(lake={"distance_m": 100, "size": 9},
                         river={"distance_m": 9000}), NATURE)
    assert r.misses == []
    assert r.state == f.MATCH


def test_river_too_far_misses_when_lake_is_also_too_far():
    r = f.evaluate(_with(lake={"distance_m": 5000, "size": 9},
                         river={"distance_m": 9000}), NATURE)
    assert {m.field for m in r.misses} == {"max_lake_m", "max_river_m"}
    assert r.state == f.REJECT


def test_lake_below_minimum_size_misses():
    r = f.evaluate(_with(lake={"distance_m": 100, "size": 2},
                         river={"distance_m": 100}), NATURE)
    assert [m.field for m in r.misses] == ["min_lake_ha"]
    assert r.state == f.REJECT


@pytest.mark.parametrize("nature", [{}, {"located": False, "note": "x"}])
def test_unlocated_listing_is_not_told_there_is_no_lake(nature):
    """assess_nature returns {"located": False} when the place cannot be
    resolved or the gazetteer is empty — reachable on every listing during a
    fresh install, since the layer download and the first mailbox poll run
    concurrently. Reporting that as "ežero nerasta" asserts something false
    about the world and discards the listing on the strength of it."""
    r = f.evaluate({**GOOD, "nature": nature}, NATURE)
    texts = [m.text for m in r.misses]
    assert not any("nerasta" in t for t in texts), \
        "missing data must not be reported as a missing lake"
    assert any("vietos nustatyti nepavyko" in t for t in texts)
    assert len([m for m in r.misses if m.field in ("max_lake_m", "max_river_m",
                                                   "min_lake_ha")]) == 1


@pytest.mark.parametrize("nature", [{}, {"located": False, "note": "x"}])
def test_unlocated_listing_is_still_kept_out_of_the_near_tier(nature):
    r = f.evaluate({**GOOD, "nature": nature}, NATURE)
    assert r.state == f.REJECT


def test_a_located_listing_with_real_nature_data_is_unaffected():
    r = f.evaluate(_with(lake={"distance_m": 100, "size": 9},
                         river={"distance_m": 200}), NATURE)
    assert r.misses == []
    assert r.state == f.MATCH


def test_a_profile_that_does_not_gate_on_nature_ignores_missing_nature():
    assert f.evaluate({**GOOD, "nature": {}}, PROFILE).misses == []


def test_radius_profile_hard_misses_when_the_place_cannot_be_located():
    # geocode finds nothing against the empty test database
    p = {**PROFILE, "centres": ["Utena"], "radius_km": 40}
    r = f.evaluate({**GOOD, "easting": None, "northing": None}, p)
    m = next(x for x in r.misses if x.field == "radius_km")
    assert m.kind == f.HARD


def test_centres_without_a_radius_are_ignored():
    p = {**PROFILE, "centres": ["Utena"], "radius_km": None}
    assert f.evaluate(GOOD, p).misses == []


GROUPED = {**PROFILE, "require_any": [
    {"name": "miškas", "words": ["mišk", "giri"]},
    {"name": "vanduo", "words": ["ežer", "upė"]},
]}


def test_all_groups_hit_is_a_match():
    r = f.evaluate({**GOOD, "title": "Sodyba miške prie ežero"}, GROUPED)
    assert r.state == f.MATCH


def test_one_group_of_two_is_a_soft_miss():
    r = f.evaluate({**GOOD, "title": "Sodyba miško apsuptyje"}, GROUPED)
    m = next(x for x in r.misses if x.field == "require_any")
    assert m.kind == f.SOFT
    assert "vanduo" in m.text


def test_no_group_hit_is_a_hard_miss():
    r = f.evaluate({**GOOD, "title": "Mūrinis namas mieste"}, GROUPED)
    m = next(x for x in r.misses if x.field == "require_any")
    assert m.kind == f.HARD


def test_flat_v1_list_still_behaves_as_v1():
    flat = {**PROFILE, "require_any": ["mišk", "giri"]}
    assert f.evaluate({**GOOD, "title": "Sodyba miške"}, flat).state == f.MATCH
    r = f.evaluate({**GOOD, "title": "Namas mieste"}, flat)
    assert next(x for x in r.misses if x.field == "require_any").kind == f.HARD


def test_sanitise_upgrades_a_flat_list_to_one_group():
    p = f.sanitise({"name": "X", "require_any": ["mišk", "giri"]})
    assert p["require_any"] == [{"name": "raktažodžiai", "words": ["mišk", "giri"]}]


def test_sanitise_preserves_groups():
    p = f.sanitise({"name": "X", "require_any": [
        {"name": "vanduo", "words": ["ežer"]}]})
    assert p["require_any"] == [{"name": "vanduo", "words": ["ežer"]}]


def test_well_formed_inputs_are_unaffected():
    assert f.normalise_groups(["mišk", "giri"]) == [
        {"name": "raktažodžiai", "words": ["mišk", "giri"]}]
    assert f.normalise_groups([{"name": "vanduo", "words": ["ežer"]}]) == [
        {"name": "vanduo", "words": ["ežer"]}]


def test_normalise_groups_never_raises_on_malformed_input():
    for bad in (42, None, "mišk", [None], [1, 2, 3],
                {"name": "x", "words": ["y"]},
                [{"name": "v", "words": "ežeras"}]):
        assert f.normalise_groups(bad) == []


def test_short_words_are_dropped_but_the_group_survives():
    assert f.normalise_groups(["a", "mišk"]) == [
        {"name": "raktažodžiai", "words": ["mišk"]}]
    assert f.normalise_groups([{"name": "vanduo", "words": ["a", "ežer"]}]) == [
        {"name": "vanduo", "words": ["ežer"]}]


def test_a_group_of_only_short_words_is_dropped_entirely():
    assert f.normalise_groups(["a"]) == []
    assert f.normalise_groups([{"name": "v", "words": ["a", "b"]}]) == []


def test_no_group_ever_contains_a_single_character_word():
    # inputs chosen so groups ARE built — otherwise this asserts nothing
    for raw in (["a", "mišk"],
                [{"name": "vanduo", "words": ["a", "ežer"]}],
                ["mišk", "giri"]):
        groups = f.normalise_groups(raw)
        assert groups, f"expected at least one group for {raw!r}"
        for g in groups:
            assert all(len(w) >= f.MIN_KEYWORD_LEN for w in g["words"])


def test_sanitise_rejects_a_require_any_of_only_short_words():
    with pytest.raises(ValueError):
        f.sanitise({"name": "X", "require_any": ["a"]})


def test_mixed_list_keeps_only_the_valid_entries():
    assert f.normalise_groups(
        [{"name": "vanduo", "words": ["ežer"]}, None, 7,
         {"name": "bad", "words": "x"}]) == [
        {"name": "vanduo", "words": ["ežer"]}]


def test_sanitise_rejects_unusable_require_any():
    for bad in ("mišk", [None], [{"name": "v", "words": "ežeras"}]):
        with pytest.raises(ValueError):
            f.sanitise({"name": "X", "require_any": bad})


def test_sanitise_accepts_an_absent_require_any():
    assert f.sanitise({"name": "X"})["require_any"] == []


def test_slightly_over_price_is_near_not_rejected():
    r = f.evaluate({**GOOD, "price_eur": 21000}, PROFILE)   # ceiling 20000, +5%
    assert r.state == f.NEAR


def test_far_over_price_is_rejected():
    r = f.evaluate({**GOOD, "price_eur": 45000}, PROFILE)
    assert r.state == f.REJECT


def test_hard_miss_is_never_near():
    r = f.evaluate({**GOOD, "price_eur": 21000, "title": "dalis sodybos"}, PROFILE)
    assert r.state == f.REJECT


def test_slightly_small_plot_is_near():
    r = f.evaluate({**GOOD, "plot_ares": 24}, PROFILE)      # needs 30, -20%
    assert r.state == f.NEAR


def test_partial_keyword_group_is_near():
    r = f.evaluate({**GOOD, "title": "Sodyba miško apsuptyje"}, GROUPED)
    assert r.state == f.NEAR


def test_match_all_still_excludes_near_misses():
    assert f.match_all({**GOOD, "price_eur": 21000}, [PROFILE]) == []
