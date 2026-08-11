import pathlib

from backend.app.sources.adapters import rinka

FIX = pathlib.Path(__file__).parent / "fixtures"
CATEGORY = (FIX / "rinka_category.html").read_text(encoding="utf-8")
DETAIL = (FIX / "rinka_detail.html").read_text(encoding="utf-8")
DETAIL_DESC_FIRST = (FIX / "rinka_detail_desc_first.html").read_text(encoding="utf-8")
DETAIL_WITH_SELLER_ADS = (FIX / "rinka_detail_with_seller_ads.html").read_text(encoding="utf-8")
URL = "https://www.rinka.lt/skelbimas/parduodama-sodyba-id-5076782"
SELLER_ADS_URL = "https://www.rinka.lt/skelbimas/x-id-4941439"


def test_list_url_is_paginated():
    assert rinka.list_url(1, 200).endswith("?page=1&per_page=200")


def test_list_ids_finds_listings_only():
    ids = rinka.list_ids(CATEGORY)
    assert [i for i, _ in ids] == [5080474, 5078893, 5078522]


def test_list_ids_deduplicates():
    assert len(rinka.list_ids(CATEGORY)) == 3


def test_list_ids_returns_absolute_urls():
    assert all(u.startswith("https://www.rinka.lt/skelbimas/")
               for _, u in rinka.list_ids(CATEGORY))


def test_parse_detail_reads_the_price():
    assert rinka.parse_detail(DETAIL, URL)["price_eur"] == 60000.0


def test_parse_detail_reads_title_and_source():
    d = rinka.parse_detail(DETAIL, URL)
    assert d["title"].startswith("Parduodama sodyba Alytaus r.")
    assert d["source"] == "rinka"
    assert d["url"] == URL


def test_municipality_comes_from_the_heading_not_the_nav_dropdown():
    # The nav lists every municipality; naive extraction returns Akmenės.
    assert rinka.parse_detail(DETAIL, URL)["municipality"] == "Alytaus rajono"


def test_parse_detail_reads_areas():
    d = rinka.parse_detail(DETAIL, URL)
    assert d["house_m2"] == 81.28
    assert d["plot_ares"] == 20


# Real listings also place the description BEFORE the price block (nav,
# then <h1>, then description, then price). The original fixture only
# covered the opposite ordering, which is why a slice starting at
# class="price" silently discarded the description -- and the plot size
# with it -- on live pages. Both orderings occur on the site.

def test_parse_detail_desc_first_reads_plot_size():
    d = rinka.parse_detail(DETAIL_DESC_FIRST, URL)
    assert d["plot_ares"] == 400  # "Sklypas 4 ha" -> 4 * 100 ares


def test_parse_detail_desc_first_municipality_is_not_the_first_nav_entry():
    # Akmenės r. is the first <option> in the nav dropdown. Confirm the
    # wider slice (from <h1> onward) still does not surface it.
    d = rinka.parse_detail(DETAIL_DESC_FIRST, URL)
    assert d["municipality"] != "Akmenės rajono"
    assert d["municipality"] == "Kauno rajono"


def test_content_slice_never_leaks_the_nav_dropdown_municipality():
    # Explicit regression check for both markup orderings, rather than
    # reasoning about it: the nav sits above <h1> in both fixtures, so
    # slicing from <h1> must exclude it either way.
    assert rinka.parse_detail(DETAIL, URL)["municipality"] != "Akmenės rajono"
    assert rinka.parse_detail(DETAIL_DESC_FIRST, URL)["municipality"] != "Akmenės rajono"


def test_parse_detail_tidies_a_punctuation_run_left_by_flattened_markup():
    # Observed live: a <span> nested between "sodyba," and "." flattens to
    # nothing, leaving "sodyba, . Vienkemis" in the title. Tidy that up
    # rather than shipping the stray ", .".
    html = (
        '<html><body><h1>Išskirtinė sodyba,<span class="icon"></span>'
        ' . Vienkemis. Apsodinta ąžuolais, beržais, '
        'spygliuočiais.</h1>'
        '<div class="description">Kaina neskelbiama.</div></body></html>'
    )
    d = rinka.parse_detail(html, URL)
    assert ", ." not in d["title"]
    assert d["title"] == (
        "Išskirtinė sodyba. Vienkemis. Apsodinta ąžuolais, "
        "beržais, spygliuočiais."
    )


def test_parse_detail_does_not_relabel_a_city_municipality_as_a_district():
    # rinka.lt lists city flats alongside rural property. "Kauno m. sav." and
    # "Kauno rajono" are different municipalities, and dedupe compares this
    # field exactly, so the adapter must not turn one into the other.
    html = ('<html><body><h1>Parduodamas butas Kauno m. sav.</h1>'
            '<div class="description">2 kambariai, 45 kv. m.</div>'
            '<span class="price">Kaina: 45000,00 &euro;</span></body></html>')
    assert rinka.parse_detail(html, URL)["municipality"] == "Kauno miesto"


def test_parse_detail_still_reads_a_district_from_the_heading():
    html = ('<html><body><h1>Parduodama sodyba Ignalinos r.</h1>'
            '<div class="description">Sklypas 30 arų.</div>'
            '<span class="price">Kaina: 17000,00 &euro;</span></body></html>')
    assert rinka.parse_detail(html, URL)["municipality"] == "Ignalinos rajono"


def test_parse_detail_reads_a_district_from_the_raj_abbreviation_in_the_heading():
    # Live rinka.lt listings polled 2026-08-10 titled "Parduodama
    # Sodyba/vienkiemis Lazdijų raj, ..." -- this is the actual path those
    # listings take (heading -> parsers.municipality_from), not a synthetic
    # regex-only case.
    html = ('<html><body><h1>Parduodama Sodyba/vienkiemis Lazdijų raj, Some Place</h1>'
            '<div class="description">Sklypas 30 arų.</div>'
            '<span class="price">Kaina: 17000,00 &euro;</span></body></html>')
    assert rinka.parse_detail(html, URL)["municipality"] == "Lazdijų rajono"


# A rinka.lt listing page also renders the same seller's other adverts below
# the main listing, each with its own place name. LOCALITY_RE.search takes
# the first match in the text, so an unbounded slice can hand this listing a
# village from a neighbouring advert -- observed live: two listings 250 km
# apart both picked up "Valėniškių k." from a third property the same seller
# advertised near a lake. locality feeds advisor.assess_nature's water/
# forest_water scoring (the two heaviest-weighted criteria), so this is a
# precision failure, not the usual imprecision the README already warns
# about.

def test_parse_detail_does_not_leak_a_locality_from_the_sellers_other_ads():
    d = rinka.parse_detail(DETAIL_WITH_SELLER_ADS, SELLER_ADS_URL)
    assert d["locality"] is None


def test_parse_detail_reads_its_own_price_not_a_neighbours():
    d = rinka.parse_detail(DETAIL_WITH_SELLER_ADS, SELLER_ADS_URL)
    assert d["price_eur"] == 17500.0


def test_parse_detail_seller_ads_fixture_municipality_still_from_heading():
    # Regression guard: the fix touches only where the content slice ends,
    # so municipality (read from the <h1>) must be unaffected.
    d = rinka.parse_detail(DETAIL_WITH_SELLER_ADS, SELLER_ADS_URL)
    assert d["municipality"] == "Telšių rajono"


def test_content_truncates_before_a_different_listing_id():
    sliced = rinka._content(DETAIL_WITH_SELLER_ADS, 4941439)
    # Neither of the seller's other listing ids should survive the cut.
    assert "4941440" not in sliced
    assert "4941441" not in sliced
    assert "Valėniškių" not in sliced


def test_content_keeps_whole_slice_when_no_other_listing_links_present():
    # Both original detail fixtures carry no links to other listings, so
    # the new truncation must not change their existing, already-tested
    # behaviour -- this is an explicit check on top of that.
    assert rinka._content(DETAIL, 5076782) == rinka._content(DETAIL, None)
    assert rinka._content(DETAIL_DESC_FIRST, 5076782) == rinka._content(DETAIL_DESC_FIRST, None)


def test_parse_detail_with_url_missing_an_id_does_not_raise():
    # If the id cannot be parsed from the URL, truncate nothing rather than
    # raising -- the whole slice (including the seller's other ads) survives,
    # same as before this fix, rather than crashing the poll.
    url_without_id = "https://www.rinka.lt/skelbimas/parduodama-sodyba"
    d = rinka.parse_detail(DETAIL_WITH_SELLER_ADS, url_without_id)
    assert d["price_eur"] == 17500.0
