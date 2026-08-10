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
