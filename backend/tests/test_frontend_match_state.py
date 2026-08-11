"""Source-text checks over the frontend for Task 12.

There is no JS runtime in this test suite (no node, no jsdom, no build step —
see AGENT.md section 3), so app.js cannot be executed here. These are
static assertions over the source text: they catch a missing wire-up or an
unescaped interpolation, but they do not prove the browser renders correctly.
See task-12-report.md for what was and was not verified by other means.
"""
import pathlib
import re

FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "frontend"
APP_JS = (FRONTEND / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (FRONTEND / "index.html").read_text(encoding="utf-8")


def test_filter_query_emits_match_state():
    assert "put('match_state', $('fMatchState').value)" in APP_JS


def test_index_declares_the_match_state_select_with_match_as_default():
    assert '<select id="fMatchState">' in INDEX_HTML
    # "match" must be the first <option> so an untouched control still
    # defaults to the pre-Task-12 behaviour (only full matches).
    select_block = INDEX_HTML.split('<select id="fMatchState">', 1)[1].split('</select>', 1)[0]
    first_option = select_block.strip().splitlines()[0]
    assert 'value="match"' in first_option


def test_table_header_gains_atitikimas_column_before_verdiktas():
    thead = INDEX_HTML.split('<thead>', 1)[1].split('</thead>', 1)[0]
    assert thead.index('>Atitikimas<') < thead.index('>Verdiktas<')


def test_match_state_change_reloads_candidates():
    """A select with no wired change handler is a control that does nothing —
    it must trigger loadCandidates like every other <select> filter does.

    This reads the wire-up list itself. It used to take the first occurrence of
    the id in app.js and look 200 characters ahead for "loadCandidates" — but
    the first occurrence is in filterQuery(), where the id is only *read*, and
    what followed it was `async function loadCandidates()` by accident of
    ordering. Inserting anything between the two broke the test without
    breaking the wiring, which is the wrong way round.
    """
    m = re.search(
        r"\[([^\]]*)\]\.forEach\(\(id\) =>\s*\$\(id\)\.addEventListener\('change', loadCandidates\)\)",
        APP_JS,
    )
    assert m, "no change-handler wire-up calling loadCandidates found in app.js"
    assert "'fMatchState'" in m.group(1)


def test_near_tier_cell_uses_match_lt_label_map():
    assert "const MATCH_LT = { match: 'Atitinka', near: 'Beveik' };" in APP_JS


def test_near_rows_get_the_is_near_class():
    assert "if (c.match_state === 'near') tr.classList.add('is-near');" in APP_JS


def test_duplicate_chip_only_fires_on_the_dublikatas_marker():
    assert "'[dublikatas'" in APP_JS
    assert "duplicateChip" in APP_JS
