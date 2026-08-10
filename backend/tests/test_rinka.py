import pathlib

from backend.app.sources.adapters import rinka

FIX = pathlib.Path(__file__).parent / "fixtures"
CATEGORY = (FIX / "rinka_category.html").read_text(encoding="utf-8")
DETAIL = (FIX / "rinka_detail.html").read_text(encoding="utf-8")
DETAIL_DESC_FIRST = (FIX / "rinka_detail_desc_first.html").read_text(encoding="utf-8")
URL = "https://www.rinka.lt/skelbimas/parduodama-sodyba-id-5076782"


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
