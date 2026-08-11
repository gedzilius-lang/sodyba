"""Source-text checks over the phone layout.

Same constraint as test_frontend_match_state.py: there is no JS runtime and no
layout engine in this suite (no node, no jsdom, no build step — AGENT.md §3), so
these assert over source text. They prove the rules are present and internally
consistent; they cannot prove the result looks right on a handset. That part was
checked by reading the rendered page on a device.

The test worth having here is the first one. Below 700px each table row becomes a
card and the <thead> is hidden, so every cell prints its own header from
data-label. Those labels are written in app.js and the headers in index.html —
two files, no shared source — so nothing but this test stops them drifting apart.
A card labelled differently from the table it replaces is worse than one with no
labels at all: it misreads as data.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP_JS = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
STYLES = (ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")

# Normalised so these do not fail the next time someone re-wraps the stylesheet.
# Whitespace is collapsed and then stripped around punctuation only — NOT
# removed outright, because a space is a descendant combinator in a selector
# (".grid thead" is a different rule from ".gridthead") and is load-bearing
# inside calc().
CSS = re.sub(r"\s*([{};:,])\s*", r"\1", re.sub(r"\s+", " ", STYLES))


def _headers(table_id: str) -> list[str]:
    """The visible text of every <th> in one table, in document order."""
    after = INDEX_HTML.split(f'id="{table_id}"', 1)[1]
    thead = after.split("<thead>", 1)[1].split("</thead>", 1)[0]
    cells = re.findall(r"<th[^>]*>(.*?)</th>", thead, re.S)
    return [re.sub(r"\s+", " ", c).strip() for c in cells]


def test_candidate_card_labels_match_the_table_headers():
    headers = _headers("candTable")
    assert len(headers) == 10, f"expected 10 candidate columns, found {headers}"
    for h in headers:
        assert f"'{h}'" in APP_JS, (
            f"column {h!r} has no matching card label in app.js — on a phone that "
            f"cell would render unlabelled or, worse, under a stale name"
        )


def test_market_card_labels_match_the_table_headers():
    for h in _headers("mktTable"):
        assert f'data-label="{h}"' in APP_JS, (
            f"market column {h!r} has no data-label in app.js"
        )


def test_the_td_helper_actually_sets_data_label():
    """Labels passed but never written to the DOM would fail silently: the
    desktop table is unaffected, so only a phone would show the damage."""
    assert "d.dataset.label = label" in APP_JS


def test_phone_layout_hides_the_header_row_and_prints_labels_instead():
    assert ".grid thead{display:none}" in CSS
    assert "content:attr(data-label)" in CSS


def test_phone_layout_stops_the_table_scrolling_sideways():
    """The card layout replaces horizontal scrolling; leaving overflow-x:auto on
    the wrapper would keep a stale scroll region around the cards."""
    assert ".table-wrap{overflow-x:visible}" in CSS


def test_form_controls_reach_16px_on_phones():
    """iOS Safari zooms the viewport when a focused control is under 16px and
    does not zoom back out — one tap on a filter and the console is off-screen."""
    block = CSS.split("@media (max-width:700px)", 1)[1]
    assert (".field input,.field select,.field textarea,.wrow input,.crow input,.srow input{font-size:16px}") in block


def test_toast_cannot_be_wider_than_the_screen():
    """It is centred on left:50%, so a fixed 520px would overhang both edges of
    a 360px phone and push the page into horizontal scrolling."""
    assert "max-width:min(520px,calc(100vw - 24px))" in CSS


def test_viewport_meta_is_present():
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in INDEX_HTML


def test_touch_targets_are_sized_for_a_coarse_pointer():
    assert "@media (pointer:coarse)" in CSS
