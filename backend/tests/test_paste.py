"""POST /api/paste must use parsers' regexes, not a stale local copy.

parsers.PRICE_RE was fixed (decimal tail, NBSP thousands separator) but the
old api.py duplicate never received that fix. That produced two failure
modes on real pasted text: a silently wrong price (0.0 EUR, which then
sorts to the top of a cost-ranked list) and an unhandled 500 on a
non-breaking-space thousands separator. These tests pin the fix by going
through the actual HTTP route, not the parser module directly.
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import router
from backend.app.db import init_db

init_db()

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def _paste(text):
    return client.post("/api/paste", json={"text": text})


def test_plain_price():
    r = _paste("17000 EUR")
    assert r.status_code == 201
    assert r.json()["price_eur"] == 17000.0


def test_decimal_tail_price():
    # rinka.lt renders exactly this; without the decimal-tail fix this
    # silently parses to 0.0 EUR instead of raising or erroring.
    r = _paste("Kaina: 60000,00 " + chr(0x20ac))
    assert r.status_code == 201
    assert r.json()["price_eur"] == 60000.0


def test_nbsp_thousands_separator_price():
    # This is the case that raised an unhandled ValueError -> HTTP 500
    # against the old local regex, which had no NBSP handling at all.
    r = _paste("17" + chr(0xa0) + "000 EUR")
    assert r.status_code == 201
    assert r.json()["price_eur"] == 17000.0


def test_space_thousands_separator_price():
    r = _paste("17 000 EUR")
    assert r.status_code == 201
    assert r.json()["price_eur"] == 17000.0


def test_dot_thousands_separator_price():
    r = _paste("17.000 EUR")
    assert r.status_code == 201
    assert r.json()["price_eur"] == 17000.0


def test_area_and_plot_still_extracted():
    r = _paste("Namas 81,28 m2, sklypas 20 arų, kaina 50000 EUR")
    assert r.status_code == 201
    body = r.json()
    assert body["house_m2"] == 81.28
    assert body["plot_ares"] == 20.0


def test_empty_text_returns_400():
    r = _paste("   ")
    assert r.status_code == 400


def test_no_price_returns_201_with_null_price():
    r = _paste("Sodyba prie ežero, gražus vaizdas")
    assert r.status_code == 201
    assert r.json()["price_eur"] is None


# The declared source must beat sniffing the text. route() picks its parser by
# scanning the body for a portal domain, so the same auction notice reported
# the starting price (25000) with its URL pasted and the market valuation
# (40000) without it — a 60% swing on one property, decided by what the user
# happened to copy.
AUCTION_WITH_URL = (
    "Varžytynės: parduodamas gyvenamasis namas Utenos r.\n"
    "Rinkos vertė 40000 EUR. Pradinė pardavimo kaina 25000 EUR.\n"
    "https://www.evarzytynes.lt/lt/turtas/12345"
)
AUCTION_NO_URL = (
    "Varžytynės: parduodamas gyvenamasis namas Utenos r.\n"
    "Rinkos vertė 40000 EUR. Pradinė pardavimo kaina 25000 EUR."
)


def test_declared_auction_source_reads_the_starting_price_with_the_url():
    r = client.post("/api/paste",
                    json={"text": AUCTION_WITH_URL, "source": "evarzytynes"})
    assert r.status_code == 201
    assert r.json()["price_eur"] == 25000.0


def test_declared_auction_source_reads_the_starting_price_without_the_url():
    # This is the case the old text-sniffing route got wrong: no domain in the
    # body, so it fell through to the generic parser and reported 40000.
    r = client.post("/api/paste",
                    json={"text": AUCTION_NO_URL, "source": "evarzytynes"})
    assert r.status_code == 201
    assert r.json()["price_eur"] == 25000.0


def test_manual_source_still_falls_back_to_sniffing_the_text():
    # "manual" names no portal format, so the URL in the body is the only
    # evidence available and must still be used.
    r = client.post("/api/paste",
                    json={"text": AUCTION_WITH_URL, "source": "manual"})
    assert r.status_code == 201
    assert r.json()["price_eur"] == 25000.0


def test_unknown_source_falls_back_to_sniffing_the_text():
    r = client.post("/api/paste",
                    json={"text": AUCTION_WITH_URL, "source": "kazkoks-portalas"})
    assert r.status_code == 201
    assert r.json()["price_eur"] == 25000.0


def test_a_classified_source_is_routed_by_its_declared_key():
    r = client.post("/api/paste", json={
        "text": "Parduodama sodyba Ignalinos r., 30 arų, 17000 EUR",
        "source": "aruodas"})
    assert r.status_code == 201
    body = r.json()
    assert body["price_eur"] == 17000.0
    assert body["municipality"] == "Ignalinos rajono"
