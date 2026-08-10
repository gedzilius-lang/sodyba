import pytest

from backend.app.sources import parsers


def _price(text):
    return parsers._f(parsers.PRICE_RE.search(text))


def test_plain_price():
    assert _price("Kaina 5000 Eur") == 5000.0


def test_space_thousands_separator():
    assert _price("17 000 EUR") == 17000.0


def test_dot_thousands_separator():
    assert _price("17.000 EUR") == 17000.0


def test_decimal_comma_tail():
    # rinka.lt renders exactly this; verified on a live listing 2026-08-10
    assert _price("Kaina: 60000,00 €") == 60000.0


def test_decimal_comma_tail_with_thousands_separator():
    assert _price("12 500,50 €") == 12500.0


def test_non_breaking_space_thousands_separator():
    assert _price("17" + chr(0xa0) + "000 EUR") == 17000.0


def test_no_price_returns_none():
    assert _price("Sodyba prie ežero") is None


def _cad(text):
    m = parsers.CAD_RE.search(text)
    return m.group(1) if m else None


def test_cadastral_one_digit_parcel():
    assert _cad("4400/0123:4") == "4400/0123:4"


def test_cadastral_two_digit_parcel():
    # The officially documented example format, and the common case in rural
    # blocks that contain few parcels -- exactly the properties this app searches for.
    assert _cad("4400/0123:45") == "4400/0123:45"


def test_cadastral_three_digit_parcel():
    assert _cad("4400/0123:456") == "4400/0123:456"


def test_cadastral_four_digit_parcel():
    assert _cad("4400/0123:0045") == "4400/0123:0045"


def test_cadastral_labelled_short_parcel():
    # Verified missed by the shipped regex before this fix.
    assert _cad("Kadastro Nr. 4152/0007:96") == "4152/0007:96"


def test_cadastral_dash_separated_unique_number_form():
    assert _cad("4400-0123-0045") == "4400-0123-0045"


def test_cadastral_five_digit_tail_does_not_match():
    # The format caps the parcel number at 4 digits. A run of 5+ contiguous
    # digits after the separator must not match at all -- not truncate to a
    # spurious 4-digit parcel, which would silently identify the wrong parcel.
    assert _cad("4400/0123:12345") is None


def test_cadastral_does_not_match_an_iso_date():
    assert _cad("Atnaujinta 2026-08-10") is None


def test_cadastral_does_not_match_a_year_range():
    assert _cad("Statybos metai 2024-2025") is None


def test_cadastral_does_not_match_a_price_with_separators():
    assert _cad("Kaina 17 000,00 EUR") is None


def test_cadastral_does_not_match_a_mobile_phone_number():
    assert _cad("Tel. +370 686 12345") is None


def test_cadastral_does_not_match_a_landline_phone_number():
    assert _cad("Tel. 8 686 12345") is None


def test_cadastral_does_not_match_an_auction_lot_reference():
    # Plausible evarzytynes.lt-style lot reference: not a cadastral number.
    # NOTE: a 5+4 digit split like this cannot match CAD_RE at all, regardless
    # of context (see test_parsers.py history / task-8b-report.md fix round 1)
    # -- it does not exercise the same-shape case-number risk that
    # test_labelled_cadastral_wins_over_an_earlier_case_number below covers.
    assert _cad("Turto vieneto Nr. 12345-6789") is None


def test_labelled_cadastral_wins_over_an_earlier_case_number():
    notice = ("Vykdomoji byla Nr. 0157/2024:12\n"
              "Parduodamas nekilnojamasis turtas: sodyba, Utenos r.\n"
              "Kadastro Nr. 4152/0007:96\n")
    assert parsers.cadastral_no(notice) == "4152/0007:96"


def test_a_bare_case_number_is_not_taken_as_a_cadastral_number():
    assert parsers.cadastral_no("Vykdomoji byla Nr. 0157/2024:12") is None


def test_lots_from_one_execution_case_do_not_share_a_cadastral_number():
    # each lot must resolve to its OWN parcel, or dedupe's authoritative
    # cadastral short-circuit would merge three unrelated properties
    lots = [("sodyba Utenos r.", "4152/0007:96"),
            ("namas Zarasu r.", "4330/0011:22"),
            ("sklypas Moletu r.", "4990/0005:7")]
    got = [parsers.cadastral_no(
               f"Vykdomoji byla Nr. 0157/2024:12\nParduodama: {what}\n"
               f"Kadastro Nr. {kad}\n15000 EUR") for what, kad in lots]
    assert got == [kad for _, kad in lots]
    assert len(set(got)) == 3


def test_a_bare_cadastral_without_any_label_is_still_found():
    assert parsers.cadastral_no("Sodyba, 4152/0007:96, 20 arų") == "4152/0007:96"


def test_unique_number_form_is_still_found():
    assert parsers.cadastral_no("Unikalus Nr. 4400-0123-0045") == "4400-0123-0045"


def test_a_contacts_footer_does_not_suppress_a_bare_cadastral_number():
    # "kontaktai" contains the old "akt" stem; it appears in nearly every
    # portal email footer, while a genuine "Akto Nr." reference is rare
    text = ("Dėl papildomos informacijos kontaktai žemiau.\n"
            "4152/0007:96 yra sklypo numeris.\n")
    assert parsers.cadastral_no(text) == "4152/0007:96"


def test_a_genuine_akto_reference_is_still_rejected():
    assert parsers.cadastral_no("Akto Nr. 1234-2026:01") is None


def test_construction_work_in_progress_does_not_suppress_a_bare_cadastral_number():
    # "vykdomi" (works in progress) shares the old "vykdom" stem with
    # "Vykdomoji byla" but is common renovation-listing language on its own
    text = "Sklype šiuo metu vykdomi statybos darbai. 4152/0007:96 yra sklypo numeris."
    assert parsers.cadastral_no(text) == "4152/0007:96"


def test_a_price_agreement_note_does_not_suppress_a_bare_cadastral_number():
    # "sutarta" (agreed) shares its root with "sutartis" (contract) via the
    # common verb "sutarti" (to agree) -- a 5-letter "sutar" stem would catch
    # this everyday listing phrase along with the rare contract reference
    text = "Kaina sutarta preliminariai. 4152/0007:96 yra sklypo numeris."
    assert parsers.cadastral_no(text) == "4152/0007:96"


def test_a_seller_decision_note_does_not_suppress_a_bare_cadastral_number():
    # "nutarė" (decided) shares its root with "nutartis" (ruling) via the
    # common verb "nutarti" (to decide)
    text = "Pardavėjas nutarė sumažinti kainą. 4152/0007:96 yra sklypo numeris."
    assert parsers.cadastral_no(text) == "4152/0007:96"


@pytest.mark.parametrize("text", [
    "Dėl informacijos kontaktai žemiau. 4152/0007:96 yra sklypo numeris.",
    "Parduodama sodyba su traktoriumi. 4152/0007:96, 20 arų.",
    "Tai faktas. Sklypas 4152/0007:96.",
    "Kontaktas: 8 600 12345. Sklypas 4152/0007:96.",
])
def test_words_merely_containing_a_noise_stem_do_not_suppress_a_cadastral(text):
    assert parsers.cadastral_no(text) == "4152/0007:96"


@pytest.mark.parametrize("text", [
    "Vykdomoji byla Nr. 0157/2024:12",
    "Akto Nr. 1234-2026:01",
    "Sutarties Nr. 1234/5678:12",
])
def test_reference_numbers_are_still_rejected(text):
    assert parsers.cadastral_no(text) is None


# A city municipality and a district municipality of the same name are two
# separate entries in config.ALL_MUNICIPALITIES, and dedupe.is_duplicate
# gates on municipality by exact match — so labelling "Kauno m. sav." as
# "Kauno rajono" both states something false and makes a city flat a merge
# candidate for a district homestead.

@pytest.mark.parametrize("text,expected", [
    ("Butas Kauno miesto sav., 2 kambariai", "Kauno miesto"),
    ("Vilniaus m. sav., Antakalnis", "Vilniaus miesto"),
    ("Parduodama sodyba Kauno r., 20 arų", "Kauno rajono"),
    ("Sodyba Utenos rajone prie ežero", "Utenos rajono"),
])
def test_municipality_from_keeps_city_and_district_apart(text, expected):
    assert parsers.municipality_from(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("Butas Kauno miesto sav., 2 kambariai, 45000 EUR", "Kauno miesto"),
    ("Vilniaus m. sav., Antakalnis, 60000 EUR", "Vilniaus miesto"),
    ("Parduodama sodyba Kauno r., 20 arų, 17000 EUR", "Kauno rajono"),
])
def test_common_reports_the_municipality_that_actually_matched(text, expected):
    assert parsers._common(text)["municipality"] == expected


def test_municipality_from_returns_none_when_nothing_matches():
    assert parsers.municipality_from("Parduodama sodyba prie ežero") is None
    assert parsers.municipality_from("") is None
