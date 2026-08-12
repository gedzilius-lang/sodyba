import pathlib
import re

import pytest

from backend.app.sources.adapters import rinka

FIX = pathlib.Path(__file__).parent / "fixtures"
CATEGORY = (FIX / "rinka_category.html").read_text(encoding="utf-8")
DETAIL = (FIX / "rinka_detail.html").read_text(encoding="utf-8")
DETAIL_DESC_FIRST = (FIX / "rinka_detail_desc_first.html").read_text(encoding="utf-8")
DETAIL_WITH_SELLER_ADS = (FIX / "rinka_detail_with_seller_ads.html").read_text(encoding="utf-8")
DETAIL_LABELLED = (FIX / "rinka_detail_labelled_fields.html").read_text(encoding="utf-8")
URL = "https://www.rinka.lt/skelbimas/parduodama-sodyba-id-5076782"
SELLER_ADS_URL = "https://www.rinka.lt/skelbimas/x-id-4941439"

# rinka_detail_live.html is a REAL rinka.lt listing page, saved 2026-08-10 and
# copied here verbatim with ONE deliberate change: the seller's phone number
# was replaced, consistently across all eight places the page spells it (5x
# "+37067132403" in data-number/tel:/sms:, 3x "867132403" in the meta tags and
# the description), with "+37061234567"/"861234567". Same length, same
# spellings, same positions — so every structural property under test here is
# exactly the live page's. (Line endings are normalised to LF, which changed
# three bytes and nothing else; parsers.to_text splits on lines either way.)
# The digits were changed because a private individual's contact number does
# not belong in git history, which is the one store the retention rule in
# api.update_candidate cannot reach.
#
# Everything else is untouched, and that is the point: an earlier hand-written
# fixture in this project put a listing's description in an order the real page
# never uses, every test passed, and the parser returned nothing useful on live
# data. Assertions below are the values the live page actually produces.
LIVE = (FIX / "rinka_detail_live.html").read_text(encoding="utf-8")
LIVE_URL = "https://www.rinka.lt/skelbimas/sodyba-prienu-r-id-4936280"


def _labelled_detail_html(muni_value: str, locality_value: str = "Plateliai") -> str:
    """A minimal detail page in the real field order: nav, <h1>, description,
    price, then a details table carrying the labelled location fields --
    matching the structure verified against 20 live listings 2026-08-10."""
    return (
        '<html><body>'
        '<nav><select><option>Akmenės r.</option></select></nav>'
        '<h1>Parduodama sodyba</h1>'
        '<div class="description">Rami sodyba kaimo pakraštyje.</div>'
        '<span class="price">Kaina: 45000,00 &euro;</span>'
        '<table class="details">'
        '<tr><td>Miestas / Rajonas:</td></tr>'
        f'<tr><td>{muni_value}</td></tr>'
        '<tr><td>Mikrorajonas / Gyvenvietė:</td></tr>'
        f'<tr><td>{locality_value}</td></tr>'
        '</table>'
        '</body></html>'
    )


def test_list_url_is_paginated():
    assert rinka.list_url("sodybos", 1, 200).endswith("?page=1&per_page=200")


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


# rinka.lt carries the location in labelled structured fields ("Miestas /
# Rajonas:" / "Mikrorajonas / Gyvenvietė:") on every listing measured
# (20/20), while free-text extraction over the heading and content block
# located a village in only 4/20 -- most Lithuanian village names simply
# don't end in the "...k." shape LOCALITY_RE looks for. Since locality
# feeds advisor.assess_nature's water/forest_water scoring (25% of the
# model's weight combined), reading the label is the fix.

def test_parse_detail_reads_municipality_and_locality_from_labelled_fields():
    d = rinka.parse_detail(DETAIL_LABELLED, URL)
    assert d["municipality"] == "Plungės rajono"
    assert d["locality"] == "Plateliai"


@pytest.mark.parametrize("label,expected", [
    ("Rietavo sav.", "Rietavo"),
    ("Plungės r. sav.", "Plungės rajono"),
    ("Vilniaus m. sav.", "Vilniaus miesto"),
    ("Kazlų Rūdos sav.", "Kazlų Rūdos"),
])
def test_parse_detail_maps_every_labelled_municipality_shape(label, expected):
    d = rinka.parse_detail(_labelled_detail_html(label), URL)
    assert d["municipality"] == expected


def test_parse_detail_labelled_dash_municipality_is_treated_as_absent():
    # rinka.lt renders an empty field as a bare "-". The heading here carries
    # no municipality either, so an absent label falls through to the
    # existing free-text path, which also finds nothing -- not the literal
    # dash.
    html = _labelled_detail_html("-")
    assert rinka.parse_detail(html, URL)["municipality"] is None


def test_parse_detail_labelled_dash_locality_is_treated_as_absent():
    html = _labelled_detail_html("Plungės r. sav.", locality_value="-")
    assert rinka.parse_detail(html, URL)["locality"] is None


def test_parse_detail_unrecognised_labelled_municipality_yields_none_not_a_guess():
    # The label is present -- the field is not omitted or "-" -- but its
    # value doesn't resolve to any real municipality. That must come back
    # None outright, not fall through to a free-text guess that might
    # coincidentally find something elsewhere on the page.
    html = _labelled_detail_html("Nežinomas r. sav.")
    assert rinka.parse_detail(html, URL)["municipality"] is None


def test_parse_detail_falls_back_to_the_heading_when_labels_are_absent():
    # No labelled table at all: the pre-fix fixtures (no details table) must
    # keep resolving municipality from the heading, exactly as before.
    assert rinka.parse_detail(DETAIL, URL)["municipality"] == "Alytaus rajono"
    assert rinka.parse_detail(DETAIL_DESC_FIRST, URL)["municipality"] == "Kauno rajono"


# --------------------------------------------------------- the real live page
# Everything below runs against rinka_detail_live.html, the saved real listing
# (see the LIVE comment at the top of this module), not a constructed one.

def test_live_page_still_parses_the_fields_the_adapter_already_had():
    # Guard rail for the fixture itself: if a future edit breaks these, the
    # date/contact assertions below are being made against a page that no
    # longer resembles the one that was saved.
    d = rinka.parse_detail(LIVE, LIVE_URL)
    assert d["price_eur"] == 6000.0
    assert d["plot_ares"] == 118.0
    assert d["municipality"] == "Prienų rajono"
    assert d["title"] == "Sodyba prienu r."


def test_live_page_listed_at_comes_from_the_info_block():
    # The page writes "2021 07 05" beside the location; stored as ISO.
    assert rinka.parse_detail(LIVE, LIVE_URL)["listed_at"] == "2021-07-05"


def test_the_sellers_member_since_date_is_not_mistaken_for_the_listing_date():
    """The trap this extraction exists to avoid.

    The saved page carries "Nuo 2021 07 05" under the seller's name — when the
    MEMBER joined rinka.lt — and on this particular page it coincides exactly
    with the date the advert went online, so a bare date regex looks correct.
    Move the member-since date and the two facts separate: listed_at must still
    be the infoBlock's date, and must not have drifted to the seller panel's.
    """
    moved = LIVE.replace("Nuo 2021 07 05", "Nuo 2019 03 11")
    assert "Nuo 2019 03 11" in moved                      # the edit landed
    d = rinka.parse_detail(moved, LIVE_URL)
    assert d["listed_at"] == "2021-07-05"
    assert d["listed_at"] != "2019-03-11"


def test_no_info_block_date_yields_none_not_the_member_since_date():
    """The other half of the same trap: with the infoBlock date gone, a loose
    date scan would happily return the seller's member-since date instead.
    None is the honest answer."""
    moved = LIVE.replace("Nuo 2021 07 05", "Nuo 2019 03 11")
    stripped = re.sub(r"(&#xE878;</i>)\s*2021 07 05", r"\1", moved)
    assert "2021 07 05" not in stripped
    assert "Nuo 2019 03 11" in stripped                   # still on the page
    assert rinka.parse_detail(stripped, LIVE_URL)["listed_at"] is None


def test_live_page_phone_is_extracted_in_full_despite_the_reveal_button():
    # The UI shows "+3706... rodyti visą" — truncated behind a reveal — but the
    # full number is in the markup, and that is what gets stored.
    d = rinka.parse_detail(LIVE, LIVE_URL)
    assert d["contact_phone"] == "+37061234567"
    assert "..." not in d["contact_phone"]


def test_live_page_phone_is_still_found_when_only_the_description_carries_it():
    # Strip every structured carrier (the reveal button's data-number and the
    # mobile tel:/sms: links) and the description's "teirautis tel.861234567"
    # is the fallback — the same number, same canonical form.
    no_widgets = (LIVE.replace('data-number="+37061234567"', 'data-number=""')
                      .replace('href="tel:+37061234567"', 'href="tel:"')
                      .replace('href="sms:+37061234567"', 'href="sms:"'))
    assert "+37061234567" not in no_widgets
    assert rinka.parse_detail(no_widgets, LIVE_URL)["contact_phone"] == "+37061234567"


def test_live_page_has_no_email_and_none_is_invented():
    # No email appears on any of the 23 live listings measured 2026-08-10 —
    # the seller panel shows only a "confirmed email" badge. None, not "".
    d = rinka.parse_detail(LIVE, LIVE_URL)
    assert d["contact_email"] is None


def test_an_email_on_the_page_is_extracted_when_one_is_actually_there():
    with_mail = LIVE.replace("Kaina sutartine",
                             "Kaina sutartine. Rasykite Vardas.Pavarde@gmail.com")
    assert rinka.parse_detail(with_mail, LIVE_URL)["contact_email"] == "vardas.pavarde@gmail.com"


def test_the_portals_own_support_address_is_not_taken_as_the_sellers():
    with_mail = LIVE.replace("Kaina sutartine", "Kaina sutartine. info@rinka.lt")
    assert rinka.parse_detail(with_mail, LIVE_URL)["contact_email"] is None


def test_the_pages_analytics_id_is_not_read_as_a_phone_number():
    """The live page carries "UA-128041834-1" in a Google Analytics <script>.
    A bare "8 followed by eight digits" pattern matches inside it. Two things
    stop it: the content slice starts at <h1>, below the script, and the
    national-number leading-digit rule rejects it anyway."""
    from backend.app.sources import parsers
    assert "UA-128041834-1" in LIVE
    assert parsers.phones_in("UA-128041834-1") == []
    planted = LIVE.replace("Kaina sutartine", "Kaina sutartine UA-128041834-1")
    assert rinka.parse_detail(planted, LIVE_URL)["contact_phone"] == "+37061234567"


def test_a_page_without_contacts_or_a_date_returns_none_for_all_three():
    html = ('<html><body><h1>Parduodama sodyba Ignalinos r.</h1>'
            '<div class="description">Sklypas 30 arų.</div>'
            '<span class="price">Kaina: 17000,00 &euro;</span></body></html>')
    d = rinka.parse_detail(html, URL)
    assert d["listed_at"] is None
    assert d["contact_phone"] is None
    assert d["contact_email"] is None
