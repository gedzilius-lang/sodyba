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


# "raj" is a very common informal abbreviation alongside "r." and the
# spelled-out "rajon-" forms, but until now MUNI_RE only accepted the latter
# two. Two live rinka.lt listings polled 2026-08-10 were titled "Parduodama
# Sodyba/vienkiemis Lazdijų raj, ..." and "... Telšių raj, ...", and both
# missed a municipality entirely (a permissive gap: filters.evaluate skips
# the check when municipality is None, and dedupe.is_duplicate's exact-match
# identity gate can then never pair the listing with its twin from another
# portal).

@pytest.mark.parametrize("text,expected", [
    ("Lazdijų raj.", "Lazdijų rajono"),
    ("Lazdijų raj", "Lazdijų rajono"),
    ("Telšių raj, Kalniškių Kaime", "Telšių rajono"),
])
def test_municipality_from_accepts_the_raj_abbreviation(text, expected):
    assert parsers.municipality_from(text) == expected


@pytest.mark.parametrize("text,expected", [
    # Every form that already worked must keep working, unchanged, after
    # "raj" is added to the alternation -- "raj" is a strict prefix of
    # "rajon", so a naive ordering mistake could truncate a match rather
    # than extend it.
    ("Lazdijų r.", "Lazdijų rajono"),
    ("Lazdijų rajone", "Lazdijų rajono"),
    ("Lazdijų rajono", "Lazdijų rajono"),
    ("Alytaus r. sav.", "Alytaus rajono"),
    ("Vilniaus m. sav.", "Vilniaus miesto"),
])
def test_municipality_from_existing_forms_are_unaffected_by_the_raj_fix(text, expected):
    assert parsers.municipality_from(text) == expected


@pytest.mark.parametrize("text", [
    # "raj" is short, so check it cannot fire on ordinary prose that merely
    # happens to contain those three letters without meaning "rajonas".
    "Garažas",
    "Kraujas",
])
def test_municipality_from_the_raj_abbreviation_does_not_fire_on_unrelated_words(text):
    assert parsers.municipality_from(text) is None


# A regex match is not proof the capture names a real municipality. Live:
# a rinka.lt listing's labelled "Mikrorajonas / Gyvenvietė:" field satisfied
# MUNI_RE on the word "Mikrorajonas" itself and was formatted into "Mikro
# rajono", which does not exist -- and municipality is a strict identity
# gate in dedupe.is_duplicate and a filter criterion, so an invented value
# is worse than None: None is honestly unknown and skips the check, while a
# fabricated name silently fails every municipality-gated profile.

@pytest.mark.parametrize("text", [
    "Mikrorajonas / Gyvenvietė",
    "Naujas rajonas",
])
def test_municipality_from_rejects_a_match_that_is_not_a_real_municipality(text):
    assert parsers.municipality_from(text) is None


@pytest.mark.parametrize("text,expected", [
    # Every form that worked before validation was added must still work --
    # this is a whitelist check layered on top of the existing regexes, not
    # a replacement for them.
    ("Lazdijų r.", "Lazdijų rajono"),
    ("Lazdijų raj.", "Lazdijų rajono"),
    ("Lazdijų raj", "Lazdijų rajono"),
    ("Lazdijų rajone", "Lazdijų rajono"),
    ("Lazdijų rajono", "Lazdijų rajono"),
    ("Lazdijų r. sav.", "Lazdijų rajono"),
    ("Vilniaus m. sav.", "Vilniaus miesto"),
    ("Alytaus r. sav.", "Alytaus rajono"),
])
def test_municipality_from_still_accepts_every_real_form_after_validation(text, expected):
    assert parsers.municipality_from(text) == expected


# municipality_from_label maps a portal's structured address field (not free
# prose) to the ALL_MUNICIPALITIES form -- see rinka.py, which reads
# rinka.lt's "Miestas / Rajonas:" field this way.

@pytest.mark.parametrize("value,expected", [
    ("Rietavo sav.", "Rietavo"),
    ("Plungės r. sav.", "Plungės rajono"),
    ("Vilniaus m. sav.", "Vilniaus miesto"),
    ("Kazlų Rūdos sav.", "Kazlų Rūdos"),
])
def test_municipality_from_label_maps_the_registers_suffixes(value, expected):
    assert parsers.municipality_from_label(value) == expected


def test_municipality_from_label_rejects_an_unrecognised_value():
    assert parsers.municipality_from_label("Nežinomas r. sav.") is None


@pytest.mark.parametrize("value", ["-", "", None])
def test_municipality_from_label_treats_dash_and_empty_as_absent(value):
    assert parsers.municipality_from_label(value) is None


# ------------------------------------------------------------------ contacts
# The whole point of normalising: Lithuania's national trunk prefix "8" stands
# in for the "+370" country code, so one seller's one phone gets written at
# least four ways across a page (data-number, tel:, sms:, free text). Stored
# unnormalised, one seller looks like several.

ONE_NUMBER = [
    "+37067132403",          # the tel:/sms: and data-number form
    "+370 671 32403",        # the same with the usual grouping
    "867132403",             # trunk form, as the description writes it
    "8 671 32403",           # trunk form, grouped
    "8-671-32403",           # trunk form, hyphenated
    "(8-671) 32403",         # trunk form, bracketed
    "0037067132403",         # international dialling prefix
    "67132403",              # the bare national number
]


@pytest.mark.parametrize("spelling", ONE_NUMBER)
def test_every_spelling_of_one_number_normalises_to_the_same_value(spelling):
    assert parsers.normalise_phone(spelling) == "+37067132403"


def test_the_spellings_really_do_collapse_to_a_single_stored_value():
    # The property that matters, stated directly rather than inferred from the
    # parametrised cases above: the set has one element.
    assert len({parsers.normalise_phone(s) for s in ONE_NUMBER}) == 1


@pytest.mark.parametrize("junk", [
    None, "", "-", "labas",
    "128041834-1",       # the Google Analytics id on the live listing page
    "8041834-1",         # the tail of that same id
    "6000,00",           # a price
    "2021 07 05",        # the listing date
    "880012345",         # freephone 800 range: not a seller's number
    "870012345",         # service 700 range: likewise
    "8671324",           # too short
    "86713240312",       # too long
])
def test_things_that_are_not_phone_numbers_come_back_none(junk):
    assert parsers.normalise_phone(junk) is None


def test_phones_in_finds_the_number_inside_running_text():
    text = "dėl sodybos apžiūros teirautis tel.867132403. Kaina sutartine"
    assert parsers.phones_in(text) == ["+37067132403"]


def test_phones_in_reports_one_number_once_however_often_it_is_spelled():
    text = "Skambinti +37067132403 arba 8 671 32403, sms 867132403"
    assert parsers.phones_in(text) == ["+37067132403"]


def test_phones_in_ignores_the_analytics_id_and_the_price():
    assert parsers.phones_in("UA-128041834-1 Kaina: 6000,00 EUR, 2021 07 05") == []


def test_phones_in_does_not_stitch_digits_across_a_line_break():
    # Separators exclude the newline on purpose: "8" ending one line and eight
    # digits starting the next are not a phone number.
    assert parsers.phones_in("kaina 8\n671 32403 EUR") == []


def test_emails_in_finds_nothing_when_there_is_nothing_to_find():
    assert parsers.emails_in("Vartotojas patvirtinęs savo elektroninį paštą") == []


def test_emails_in_lowercases_and_deduplicates():
    assert parsers.emails_in("Rasykite Vardas@Gmail.com arba vardas@gmail.com.") == \
        ["vardas@gmail.com"]


def test_the_leading_zero_trunk_form_real_sellers_type_is_accepted():
    """Lithuania's trunk prefix is 8, not the 0 most of Europe uses, but one of
    the 35 live listings collected 2026-08-10 publishes "061154699" in the
    portal's own contact widget. Structured contact fields are taken at their
    word; free text is not (see the test below)."""
    assert parsers.normalise_phone("061154699") == "+37061154699"
    assert parsers.normalise_phone("0 611 54699") == "+37061154699"


def test_the_free_text_scanner_does_not_guess_at_a_bare_leading_zero():
    # In running prose a nine-digit run starting 0 is a guess, and a guessed
    # phone number gets dialled. PHONE_RE requires +370, 00370 or 8.
    assert parsers.phones_in("kodas 061154699 sklypui") == []
    assert parsers.phones_in("skambinti 861154699") == ["+37061154699"]


# ------------------------------------------------------------ portal coverage
# ALERT_ONLY in registry.py means the lawful route into this app is the
# portal's own email alerts — which arrive through sources/mailbox.py and are
# extracted here. A portal declared ALERT_ONLY with no parser is a
# subscription the operator can create, and that will then be ingested as
# nothing recognisable: kampas.lt was exactly that until this test existed.

def _alert_only_keys() -> list[str]:
    from backend.app.sources import registry
    return sorted(s.key for s in registry.SOURCES
                  if s.policy == registry.ALERT_ONLY)


def test_every_alert_only_portal_has_a_parser():
    missing = [k for k in _alert_only_keys() if parsers.parser_for(k) is None]
    assert not missing, f"ALERT_ONLY portals with no parser: {missing}"


def test_every_alert_only_portal_is_recognisable_from_its_own_domain():
    """The mailbox path names the portal from the sender address, so a parser
    that no address can reach is unreachable in practice."""
    from backend.app.sources import registry
    for s in registry.SOURCES:
        if s.policy != registry.ALERT_ONLY:
            continue
        assert parsers.source_for_alert(f"alerts@{s.host}", "") == s.key, \
            f"an alert from {s.host} does not route to „{s.key}“"


def test_every_routed_sender_names_a_parser():
    unknown = [key for _, key in parsers.ALERT_SENDERS
               if parsers.parser_for(key) is None]
    assert not unknown, f"routed sources with no parser: {unknown}"


def test_a_kampas_alert_parses_as_kampas():
    d = parsers.parse_kampas(
        "Sodyba Ukmergės r., Deltuvos k.\n"
        "Kaina 16 750 EUR\n"
        "Namas 105 m2, sklypas 25 arai\n"
        "https://www.kampas.lt/skelbimai/namai/deltuva-1234567")
    assert d["source"] == "kampas"
    assert d["url"] == "https://www.kampas.lt/skelbimai/namai/deltuva-1234567"
    assert d["price_eur"] == 16750
    assert d["house_m2"] == 105
    assert d["plot_ares"] == 25
    assert d["municipality"] == "Ukmergės rajono"
    assert d["locality"] == "Deltuvos k."
