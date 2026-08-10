import pathlib

from backend.app.sources.adapters import rinka

FIX = pathlib.Path(__file__).parent / "fixtures"
CATEGORY = (FIX / "rinka_category.html").read_text(encoding="utf-8")
DETAIL = (FIX / "rinka_detail.html").read_text(encoding="utf-8")
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
