"""Source-text checks over the narrow layouts.

Same constraint as test_frontend_match_state.py: there is no JS runtime and no
layout engine in this suite (no node, no jsdom, no build step — AGENT.md §3), so
these assert over source text. They prove the rules are present and internally
consistent; they cannot prove the result looks right on a handset. In
particular, nothing here can measure a rendered column, so nothing here can
prove the table fits — the widths below are asserted to be *declared and to
total 100%*, which is what makes `table-layout:fixed` behave, not that the
content inside them is legible. That half was checked by driving Chrome at
1241, 1440, 1024, 820, 768, 390 and 360px and reading the screenshots.

The test worth having here is still the first one. Below 1240px each table row
becomes a card and the <thead> is hidden, so every cell prints its own header
from data-label. Those labels are written in app.js and the headers in
index.html — two files, no shared source — so nothing but this test stops them
drifting apart. A card labelled differently from the table it replaces is worse
than one with no labels at all: it misreads as data.
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

# The width at which rows stop being table rows and become cards. Named once
# here so the tests below read as statements about the layout rather than about
# a number, and so a deliberate move shows up as one edit.
CARDS_BELOW = 1240
RAIL_STACKS_BELOW = 1080


def _media(header: str) -> str:
    """The body of one @media block, brace-matched so a nested rule cannot end
    it early."""
    assert header in CSS, f"no {header!r} block in styles.css"
    start = CSS.index(header) + len(header)
    depth, i = 0, start
    while True:
        if CSS[i] == "{":
            depth += 1
        elif CSS[i] == "}":
            depth -= 1
            if depth == 0:
                return CSS[start + 1:i]
        i += 1


def _decls(block: str, selector: str) -> str:
    """The declarations of one rule inside a block."""
    m = re.search(re.escape(selector) + r"\{([^{}]*)\}", block)
    assert m, f"no rule for {selector!r}"
    return m.group(1)


def _fn(name: str) -> str:
    """Source of one named function in app.js, matched by brace depth."""
    m = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", APP_JS)
    assert m, f"no function named {name!r} in app.js"
    start, depth, i = m.end(), 1, m.end()
    while depth:
        if APP_JS[i] == "{":
            depth += 1
        elif APP_JS[i] == "}":
            depth -= 1
        i += 1
    return APP_JS[start:i]


def _headers(table_id: str) -> list[str]:
    """The visible text of every <th> in one table, in document order."""
    after = INDEX_HTML.split(f'id="{table_id}"', 1)[1]
    thead = after.split("<thead>", 1)[1].split("</thead>", 1)[0]
    cells = re.findall(r"<th[^>]*>(.*?)</th>", thead, re.S)
    return [re.sub(r"\s+", " ", c).strip() for c in cells]


def test_candidate_card_labels_match_the_table_headers():
    headers = _headers("candTable")
    assert len(headers) == 9, f"expected 9 candidate columns, found {headers}"
    for h in headers:
        assert f"'{h}'" in APP_JS, (
            f"column {h!r} has no matching card label in app.js — on a phone that "
            f"cell would render unlabelled or, worse, under a stale name"
        )


def test_the_price_context_rides_inside_the_labelled_price_cell():
    """Listing age and the peer ratio went into KAINA rather than into columns
    of their own, so there is no new cell to drift out of sync with a <th> —
    but the two context lines still have to survive the card layout, where the
    cell becomes a flex row with its label printed on the left. They collapse
    onto one line beside the price there, the way EUR/tšk. already does."""
    assert "td(priceCell(c), 'num', 'Kaina')" in APP_JS
    assert "Kaina" in _headers("candTable")
    block = _media(f"@media (max-width:{CARDS_BELOW}px)")
    assert ".ctx{display:inline;margin-left:8px}" in block


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


# --------------------------------------------------- the table fits, or it is
# not a table
#
# The nine columns settled at a min-content width of 1173px under the default
# auto table layout and simply stopped shrinking, so from 700px — where cards
# used to start — up to and past 1440px the last columns were cut off by their
# scroll container. ATITIKIMAS and VERDIKTAS are the two that went first, which
# is the worst possible pair to lose: the verdict is the answer the user came
# for. Two things fix it together and both are asserted here, because either
# alone leaves a band of widths broken.
def test_the_candidate_table_cannot_outgrow_its_container():
    """table-layout:fixed makes the declared widths authoritative, so the table
    is exactly as wide as the space it is given at any viewport rather than as
    wide as its longest cell wants to be."""
    assert "table-layout:fixed" in _decls(CSS, ".grid")


def test_the_candidate_columns_declare_widths_that_total_the_table():
    """Under fixed layout an incomplete or over-subscribed set of widths is
    silently redistributed, which is how a column quietly loses its room."""
    widths = re.findall(r"#candTable th:nth-child\((\d+)\)\{width:(\d+)%\}", CSS)
    assert [int(n) for n, _ in widths] == list(range(1, 10)), (
        f"every one of the 9 columns needs a declared width, found {widths}"
    )
    assert sum(int(w) for _, w in widths) == 100


def test_rows_become_cards_below_the_width_the_columns_need():
    """The card layout was already written and already good; the fix for the
    overflow was to let it cover every viewport that is not a wide desktop,
    rather than only handsets."""
    block = _media(f"@media (max-width:{CARDS_BELOW}px)")
    assert ".grid thead{display:none}" in block
    assert "content:attr(data-label)" in block
    assert ".table-wrap{overflow-x:visible}" in block
    assert ".grid,.grid tbody,.grid tr,.grid td{display:block;width:auto}" in block


def test_no_column_is_dropped_when_rows_become_cards():
    """A card that shows six of nine fields is a different failure from a table
    that clips three — the point of cards here is that nothing is lost."""
    card_rules = _media(f"@media (max-width:{CARDS_BELOW}px)")
    assert "display:none" not in card_rules.replace(".grid thead{display:none}", "")


# ------------------------------------------------ content before its controls
def test_the_rail_stops_preceding_the_content_once_it_stacks():
    """Below this width the rail is no longer a column beside the content, it
    is a block above it — and ten filter controls above the candidates means
    opening the app to its controls and scrolling past every one of them to
    reach the first property."""
    block = _media(f"@media (max-width:{RAIL_STACKS_BELOW}px)")
    assert ".rail{display:contents}" in block
    assert "#filterPanel{order:1}" in block
    assert ".content{order:2}" in block
    assert ".rail-block{order:3}" in block


def test_the_filter_panel_is_a_disclosure_open_by_default_in_the_markup():
    """`open` in the markup means a desktop renders it expanded before app.js
    has run — no flash of a collapsed panel, and it still works with no JS."""
    assert '<details class="rail-block" id="filterPanel" open>' in INDEX_HTML
    assert '<summary class="eyebrow">' in INDEX_HTML
    assert '<span id="filterCount"' in INDEX_HTML or 'id="filterCount"' in INDEX_HTML


def test_the_disclosure_is_collapsed_wherever_the_rail_stacks():
    assert "$('filterPanel').open = false" in APP_JS


def test_the_collapse_width_in_js_matches_the_reorder_width_in_css():
    """Two files, no shared source, same decision — exactly the drift the
    data-label test above exists for. If these separate, some viewport gets a
    stacked rail with the panel expanded (the bug) or a side rail with it
    collapsed (a control hidden for no reason)."""
    m = re.search(r"matchMedia\('\(max-width:(\d+)px\)'\)\.matches\) \$\('filterPanel'\)", APP_JS)
    assert m, "app.js must decide the collapse from a max-width media query"
    assert int(m.group(1)) == RAIL_STACKS_BELOW
    assert "#filterPanel{order:1}" in _media(f"@media (max-width:{m.group(1)}px)")


def test_the_summary_states_how_many_filters_are_active():
    """A collapsed panel that is quietly removing rows is worse than an
    expanded one, so the count is the thing that makes collapsing honest."""
    assert "function activeFilterCount()" in APP_JS
    assert "updateFilterCount()" in _fn("loadCandidates"), (
        "the count has to be recomputed on every reload, or it goes stale the "
        "first time a filter changes"
    )
    body = _fn("updateFilterCount")
    assert "nėra" in body and "aktyv" in body


def test_the_active_filter_count_covers_the_controls_that_hide_rows():
    """The definition is the app's own: the controls "Išvalyti filtrus" resets.
    Sort order and "rodyti archyvuotus" are excluded on purpose — they reorder
    and widen the list, they never hide a candidate."""
    counted = set(re.findall(r"'(f\w+)'", _fn("activeFilterCount") +
                             APP_JS.split("const FILTER_IDS", 1)[1].split(";", 1)[0]))
    cleared = set(re.findall(r"'(f\w+)'", _fn("wire").split("btnClear", 1)[1].split("loadCandidates", 1)[0]))
    assert cleared - counted <= {"fSort", "fArchived"}, (
        f"cleared by the button but not counted as active: {cleared - counted}"
    )
    assert "fSort" not in counted and "fArchived" not in counted


# ------------------------------------------------------------- touch targets
def test_touch_targets_reach_44px_on_a_coarse_pointer():
    """20x20 checkboxes and 29px-tall buttons at every viewport including a
    phone, because the earlier pointer:coarse rules set padding and font size
    and never a minimum. Note what is asserted and what is not: a control that
    is itself the target carries min-height; a checkbox wrapped in a <label>
    does not, because the label is what the thumb lands on and the label is
    what gets sized. Measuring the input box will still read ~24px — that is
    the design, not a miss."""
    block = _media("@media (pointer:coarse)")
    for selector in (".btn", ".tab", ".check,.frow,.krow,.prow", ".ptoggle",
                     ".prow .pedit", ".field input,.field select",
                     ".wrow input,.crow input", ".rail-block>summary"):
        assert "44px" in _decls(block, selector), (
            f"{selector} is still below the 44px minimum on a touch device"
        )
    assert "height:44px" in _decls(block, ".srow input[type=range]")


def test_the_profile_toggle_has_a_label_to_be_a_target():
    """.prow is a div, so unlike .check/.frow/.krow there is no label wrapping
    the row for a tap to land on — app.js has to supply one."""
    assert 'class="ptoggle"' in APP_JS
    assert "<label class=\"ptoggle\"><input type=\"checkbox\"" in APP_JS


def test_desktop_is_not_loosened_by_the_touch_sizing():
    """All of it lives behind pointer:coarse; a mouse keeps the dense layout."""
    outside = CSS.split("@media (pointer:coarse)", 1)[0]
    assert "min-height:44px" not in outside


# ------------------------------------------------------------------ favicon
def test_a_favicon_is_declared_and_needs_no_asset():
    """Every desktop load logged a 404 for /favicon.ico. An inline SVG data URI
    keeps the no-build-step constraint: nothing to fetch, nothing to ship."""
    m = re.search(r'<link rel="icon" href="(data:image/svg\+xml,[^"]*)"', INDEX_HTML)
    assert m, "no inline <link rel=icon> in index.html"
    icon = m.group(1)
    assert "%23" in icon, "a raw # in a data URI starts a fragment and truncates the SVG"
    for colour in ("8fae5d", "c8983a", "a94f3c"):   # lichen, ochre, oxide
        assert colour in icon, f"the mark's {colour} band is missing from the icon"


# ------------------------------------------------- unscored, stated once only
def test_an_unscored_candidate_is_reported_once_not_in_two_columns():
    """BALAS and EUR/tšk. were separate columns, and every candidate is
    unscored until scores are entered by hand — so both printed an em dash on
    every row, stating the same absence twice."""
    headers = _headers("candTable")
    assert "Balas" in headers
    assert not [h for h in headers if "EUR" in h], (
        f"EUR/tšk. is back as its own column: {headers}"
    )


def test_the_score_cell_shows_the_ranking_metric_rather_than_dropping_it():
    """EUR per score point is the ranking metric (AGENT.md §8) and the default
    sort — merging the column must not demote it to a tooltip."""
    body = _fn("scoreCell")
    assert "eur_per_point" in body
    assert "EUR/tšk." in body


def test_the_score_cell_invents_nothing_for_an_unscored_candidate():
    """One dash, printed only where there is genuinely no score — and no
    fallback number anywhere in the cell."""
    body = _fn("scoreCell")
    assert body.count("—") == 1
    assert "weighted_score === null" in body
    assert not re.search(r"\|\|\s*0", body), "no zero standing in for an absent score"


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
