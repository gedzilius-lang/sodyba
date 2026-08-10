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
    assert _cad("Turto vieneto Nr. 12345-6789") is None
