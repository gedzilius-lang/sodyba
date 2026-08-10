"""parsers.py carries Lithuanian text and must survive every editor round-trip."""
import pathlib

from backend.app.sources import parsers

SRC = pathlib.Path(parsers.__file__)


def test_source_has_no_byte_order_mark():
    assert not SRC.read_bytes().startswith(b"\xef\xbb\xbf")


def test_source_is_valid_utf8():
    SRC.read_bytes().decode("utf-8")


def test_lithuanian_patterns_still_match_lithuanian_text():
    assert parsers.LOCALITY_RE.search("Girionių k.")
    assert parsers.MUNI_RE.search("Šiaulių r.")
    assert parsers.AREA_RE.search("81,28 m²")
    assert parsers.PLOT_RE.search("20 arų")
