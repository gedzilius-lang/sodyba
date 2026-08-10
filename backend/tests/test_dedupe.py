from backend.app import dedupe

A = {"source": "rinka", "municipality": "Utenos rajono", "locality": "Kirdeikių k.",
     "price_eur": 17000, "house_m2": 81.3, "plot_ares": 20,
     "title": "Parduodama sodyba Utenos r. prie ežero", "cadastral_no": None}


def test_same_listing_from_another_portal_is_a_duplicate():
    b = {**A, "source": "aruodas",
         "title": "Sodyba prie ežero, Utenos r., parduodama"}
    assert dedupe.is_duplicate(A, b)


def test_small_price_drop_is_still_the_same_listing():
    assert dedupe.is_duplicate(A, {**A, "source": "alio", "price_eur": 16600})


def test_different_property_is_not_a_duplicate():
    b = {**A, "source": "aruodas", "price_eur": 9000, "house_m2": 40,
         "title": "Namas Zarasų rajone", "municipality": "Zarasų rajono"}
    assert not dedupe.is_duplicate(A, b)


def test_same_cadastral_number_beats_every_other_signal():
    a = {**A, "cadastral_no": "4400/0123:45"}
    b = {"source": "turtas", "cadastral_no": "4400/0123:45", "price_eur": 1,
         "title": "visai kitas tekstas", "municipality": "Kitas rajonas"}
    assert dedupe.is_duplicate(a, b)


def test_different_municipality_is_never_a_duplicate():
    assert not dedupe.is_duplicate(A, {**A, "municipality": "Zarasų rajono"})


def test_find_duplicate_picks_the_match_from_a_list():
    rows = [{**A, "price_eur": 5000, "title": "kitas"},
            {**A, "source": "aruodas", "ref": "K007"}]
    assert dedupe.find_duplicate(A, rows)["ref"] == "K007"


def test_find_duplicate_returns_none_when_nothing_matches():
    assert dedupe.find_duplicate(A, [{**A, "municipality": "Zarasų rajono"}]) is None


def test_fingerprint_is_stable_and_ignores_query_strings():
    x = {"url": "https://www.rinka.lt/skelbimas/foo-id-1?utm_source=x"}
    y = {"url": "https://www.rinka.lt/skelbimas/foo-id-1"}
    assert dedupe.fingerprint(x) == dedupe.fingerprint(y)


def test_different_villages_are_never_duplicates():
    # the exact false positive this round exists to close
    a = {"source": "aruodas", "municipality": "Utenos rajono",
         "locality": "Kirdeikių k.", "price_eur": 17000, "house_m2": 80,
         "plot_ares": 40, "title": "Parduodama sodyba Utenos rajone prie ežero",
         "cadastral_no": None}
    b = {**a, "source": "rinka", "locality": "Baibių k.", "price_eur": 17300,
         "house_m2": 82, "plot_ares": 42,
         "title": "Parduodama sodyba Utenos rajone prie upės"}
    assert not dedupe.is_duplicate(a, b)


def test_district_words_in_the_title_do_not_inflate_similarity():
    assert dedupe.title_tokens("Sodyba Utenos rajone prie ežero",
                               "Utenos rajono", "Kirdeikių k.") == {"ežero"}


def test_same_property_from_another_portal_still_merges():
    a = {"source": "aruodas", "municipality": "Utenos rajono",
         "locality": "Kirdeikių k.", "price_eur": 17000, "house_m2": 80,
         "plot_ares": 40, "title": "Parduodama sodyba Utenos rajone prie ežero",
         "cadastral_no": None}
    b = {**a, "source": "rinka",
         "title": "Sodyba prie ežero, Utenos r., parduodama"}
    assert dedupe.is_duplicate(a, b)


def test_unusable_titles_merge_only_when_the_village_matches():
    base = {"municipality": "Utenos rajono", "price_eur": 17000,
            "house_m2": 80, "plot_ares": 40, "title": "Parduodama sodyba",
            "cadastral_no": None}
    same = {**base, "locality": "Kirdeikių k."}
    assert dedupe.is_duplicate(same, {**same, "source": "rinka"})
    assert not dedupe.is_duplicate({**base, "locality": None},
                                   {**base, "locality": None, "source": "rinka"})


def test_cadastral_number_still_overrides_a_locality_difference():
    a = {"municipality": "Utenos rajono", "locality": "Kirdeikių k.",
         "cadastral_no": "4400/0123:45", "price_eur": 17000, "title": "x"}
    b = {"municipality": "Kitas rajonas", "locality": "Baibių k.",
         "cadastral_no": "4400/0123:45", "price_eur": 1, "title": "kitas"}
    assert dedupe.is_duplicate(a, b)
