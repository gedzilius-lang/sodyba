"""The drawer's ŠALTINIS select must be able to display every real source.

Same constraint as the other frontend suites: no JS runtime and no layout
engine here, so this asserts over source text. That is enough for this bug,
because the bug is entirely in the source text — `rinka` had no `<option>`, and
a `<select>` handed a value it has no option for selects nothing and renders
blank. So the field read empty on all 35 stored candidates, every one of which
came from rinka.lt, and nothing anywhere reported an error: the same silent
shape as a filter that quietly stops filtering.

The list is pinned to `sources/registry.py`, which AGENT.md section 3 names as
the single authority on sources, so adding a portal there and forgetting the
drawer fails here instead of shipping another blank field.
"""
import pathlib
import re

from backend.app.sources import registry

ROOT = pathlib.Path(__file__).resolve().parents[2]
INDEX_HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

# The one registry key with no business in this list. `data_gov` is the
# open-data API the nature layers are downloaded from (get.data.gov.lt); it
# never carries a listing, so no candidate row can hold it as `source` and
# offering it would only invite a wrong answer. Every other key can reach the
# candidate table — by poller, by mailbox alert, or by hand — and so must be
# selectable.
NOT_A_LISTING_SOURCE = {"data_gov"}


def _source_select() -> str:
    m = re.search(r'<select id="dSource">(.*?)</select>', INDEX_HTML, re.S)
    assert m, "no #dSource select in index.html"
    return m.group(1)


def _option_values(block: str) -> list[str]:
    return re.findall(r'<option value="([^"]*)"', block)


def test_rinka_is_selectable():
    # The reported bug, named on its own so a regression says what broke.
    assert "rinka" in _option_values(_source_select())


def test_every_listing_source_in_the_registry_has_an_option():
    have = set(_option_values(_source_select()))
    want = {s.key for s in registry.SOURCES} - NOT_A_LISTING_SOURCE
    assert not (want - have), f"sources with no <option>: {sorted(want - have)}"


def test_no_option_names_a_source_the_registry_does_not_know():
    have = set(_option_values(_source_select()))
    known = {s.key for s in registry.SOURCES}
    assert not (have - known), f"options for unknown sources: {sorted(have - known)}"


def test_the_two_turtas_hosts_are_not_confused_for_each_other():
    # `turtas` (turtas.lt) and `aukcionai_turtas` (aukcionai.turtas.lt) are
    # different sources with different policies — POLL and LINK_ONLY. The
    # option for `turtas` was labelled with the other one's host, which made
    # the two indistinguishable in the only place a human picks between them.
    block = _source_select()
    labels = dict(re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', block))
    assert labels["turtas"] == "turtas.lt"
    assert labels["aukcionai_turtas"] == "aukcionai.turtas.lt"


def test_every_option_carries_a_label():
    block = _source_select()
    blank = [v for v, text in
             re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', block)
             if not text.strip()]
    assert not blank, f"options with no visible text: {blank}"
