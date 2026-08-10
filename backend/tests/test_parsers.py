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


def test_no_price_returns_none():
    assert _price("Sodyba prie ežero") is None
