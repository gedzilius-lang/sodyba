"""A <select> must not answer a question the row never answered.

Same constraint as the other frontend suites: no JS runtime and no layout
engine here (no node, no jsdom, no build step — AGENT.md section 3), so these
assert over source text. Be clear about the seam that leaves. Source text can
prove that the empty option exists, that openDrawer routes both drawer selects
through one helper instead of coercing them, that the helper inserts an option
carrying the stored value before it assigns `.value`, and that no source key is
hardcoded as a fallback anywhere in openDrawer. It cannot prove that Chrome
then selects that option, renders the label, or that a save round-trips. That
half was checked by driving Chrome at 1440 and 390 against the running server,
with two candidates created for the purpose and deleted afterwards, and by
reading the rows back over the API:

  before, K-row with source ""          drawer showed "evarzytynes.lt";
                                        pressing Išsaugoti and touching nothing
                                        wrote source="evarzytynes" to the row
  before, source "aruodas_lt",          both selects rendered blank; the same
         municipality "Utenos r."       untouched save left source="" and
                                        municipality=NULL
  after,  same two rows                 "— nenurodytas —"; "aruodas_lt — nėra
                                        sąraše" / "Utenos r. — nėra sąraše",
                                        and the untouched save changed neither

The bug this file exists for
---------------------------
`$('dSource').value = CURRENT.source || 'evarzytynes'` — a row with no source
was displayed as a row from evarzytynes.lt, a portal registry.py marks
ALERT_ONLY because its robots.txt forbids fetching it. So the field that
records provenance, and with it what this project is allowed to do, stated a
provenance that was not merely unknown but wrong; and because the drawer writes
back what it displays, one press of Išsaugoti made it true in the database.

The mirror of it is quieter and was found by looking: a stored value the option
list does not contain — a renamed source key, a municipality spelled the way
the advert spelled it — selects nothing at all, and `.value` then reads "", so
the next save of any other field on that row erases the real one. An unset
value must not render as some other value; a set value must not vanish.

Only these two selects show a stored value. The five filter controls (#fMuni,
#fProfile, #fVerdict, #fMatchState, #fSort) hold the operator's own choice, not
a property of any row, and their first option is the honest reading of an empty
one — "Visa Lietuva", "Visi profiliai", "Visi". They are asserted below to keep
saying that, so nobody "fixes" them into unknown states they do not have.
"""
import pathlib
import re

from backend.app.config import ALL_MUNICIPALITIES
from backend.app.sources import registry

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP_JS = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
STYLES = (ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")

CSS = re.sub(r"\s*([{};:,])\s*", r"\1", re.sub(r"\s+", " ", STYLES))

# The value both selects use for "nothing is recorded here". "" is what an
# unset source is stored as (candidate.source is NOT NULL, so it is the empty
# string rather than NULL) and what collect() turns back into NULL for the
# nullable municipality column.
UNSET = ""


def _fn(name: str) -> str:
    """Source of one `function name(...) { ... }`, brace-matched."""
    m = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", APP_JS)
    assert m, f"no function named {name!r} found in app.js"
    start, depth, i = m.end(), 1, m.end()
    while depth:
        if APP_JS[i] == "{":
            depth += 1
        elif APP_JS[i] == "}":
            depth -= 1
        i += 1
    return APP_JS[start:i]


def _uncommented(src: str) -> str:
    """Code with its comments removed.

    Needed because the comments here name the very strings the code must not
    contain — an explanation of why `evarzytynes` is gone would otherwise fail
    the test that says it is gone.
    """
    return re.sub(r"//[^\n]*", " ", re.sub(r"/\*.*?\*/", " ", src, flags=re.S))


def _options(select_id: str, html: str) -> list[tuple[str, str]]:
    m = re.search(rf'<select id="{select_id}">(.*?)</select>', html, re.S)
    assert m, f"no #{select_id} select found"
    return re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', m.group(1))


# ------------------------------------------------- the absence has an option
def test_the_source_select_offers_the_absence_of_a_source():
    values = [v for v, _ in _options("dSource", INDEX_HTML)]
    assert UNSET in values, "no <option> for a row with no source"


def test_the_absence_of_a_source_is_the_first_option():
    """First, so the empty state is the one an untouched control shows and the
    one the eye reaches first in the open list — a real portal must never be
    the default answer to a question nobody asked."""
    values = [v for v, _ in _options("dSource", INDEX_HTML)]
    assert values[0] == UNSET


def test_the_empty_source_option_cannot_be_read_as_a_source():
    """Its label must not name a host or a portal. This is the whole point:
    "—" alone would be ambiguous with a rendering gap, and anything ending in
    .lt would be a claim."""
    label = dict((v, t) for v, t in _options("dSource", INDEX_HTML))[UNSET]
    assert "nenurodyt" in label, f"empty option does not say 'unset': {label!r}"
    assert ".lt" not in label
    hosts = {s.host for s in registry.SOURCES}
    assert not any(h and h in label for h in hosts)


def test_the_municipality_select_seeds_the_same_kind_of_empty_option():
    """#dMuni is filled by app.js, not by index.html, so its empty option is
    asserted where it is written."""
    m = re.search(r"\$\('dMuni'\)\.innerHTML = '<option value=\"\">([^<]*)</option>'", APP_JS)
    assert m, "#dMuni does not seed an empty option"
    label = m.group(1)
    assert "nenurodyt" in label, f"empty option does not say 'unset': {label!r}"
    assert label not in ALL_MUNICIPALITIES


def test_the_filter_municipality_still_means_all_of_lithuania():
    """The same "" in the filter is not an unknown: it is the absence of a
    filter, and saying "nenurodyta" there would be false. Two selects, two
    meanings for the same empty value — this pins them apart."""
    m = re.search(r"\$\('fMuni'\)\.innerHTML = '<option value=\"\">([^<]*)</option>'", APP_JS)
    assert m and m.group(1) == "Visa Lietuva"


def test_the_other_filter_selects_keep_their_all_option_first():
    """#fVerdict and #fProfile are not row state either. Their first option
    means "no filter", and an untouched control landing on it is correct."""
    assert _options("fVerdict", INDEX_HTML)[0] == (UNSET, "Visi")
    assert _options("fProfile", INDEX_HTML)[0] == (UNSET, "Visi profiliai")


# ------------------------------------------------------- openDrawer coerces nothing
def test_open_drawer_hardcodes_no_source_key_as_a_fallback():
    """Not `|| 'evarzytynes'`, and not any other key either — the next one
    would be the same bug with a different portal's name on it."""
    body = _uncommented(_fn("openDrawer"))
    named = [s.key for s in registry.SOURCES if f"'{s.key}'" in body]
    assert not named, f"openDrawer names source keys: {named}"


def test_both_stored_value_selects_go_through_the_helper():
    body = _uncommented(_fn("openDrawer"))
    assert "showStoredValue($('dSource'), CURRENT.source)" in body
    assert "showStoredValue($('dMuni'), CURRENT.municipality)" in body


def test_no_stored_value_select_is_assigned_directly():
    """A direct `.value =` is how both bugs got in: it silently accepts a value
    the list has no option for."""
    body = _uncommented(_fn("openDrawer"))
    assert "$('dSource').value =" not in body
    assert "$('dMuni').value =" not in body


def test_a_new_object_declares_no_source():
    """An object being typed in has not come from anywhere yet. Pre-selecting a
    portal is how a row acquires a provenance nobody asserted — the same wrong
    claim as the display bug, arriving through the form instead."""
    body = _uncommented(_fn("openDrawer"))
    m = re.search(r"ref: null, source: '([^']*)'", body)
    assert m, "openDrawer no longer builds a blank candidate the way this test reads it"
    assert m.group(1) == UNSET


# ------------------------------------------------------------------ the helper
def test_an_unlisted_value_gets_an_option_of_its_own():
    body = _fn("showStoredValue")
    assert "createElement('option')" in body
    assert "opt.value = v" in body


def test_the_unlisted_option_is_inserted_before_the_value_is_assigned():
    """Order is the whole mechanism: assigning `.value` first would select
    nothing, and `.value` would read "" from then on."""
    body = _fn("showStoredValue")
    assert body.index("insertBefore") < body.index("sel.value = v")


def test_the_unlisted_option_says_it_is_unlisted():
    """It carries the stored text, so it cannot be confused with a listed
    source; and it says the list does not have it, so the operator can see
    there is something to correct rather than a value that merely looks odd."""
    body = _fn("showStoredValue")
    assert "nėra sąraše" in body
    assert "${v}" in body


def test_the_helper_clears_the_option_it_added_last_time():
    """The drawer is one element reused for every row. Without this, opening
    five odd rows leaves five stale options behind, and the sixth row could be
    shown one of them."""
    body = _fn("showStoredValue")
    assert "option[data-unlisted]" in body
    assert body.index("option[data-unlisted]") < body.index("createElement('option')")


def test_the_helper_builds_the_option_without_innerHTML():
    """Stored values are third-party text. Everything this app interpolates
    into innerHTML goes through esc(); this goes through neither, because it
    builds a node and sets textContent, which cannot be parsed as markup."""
    body = _fn("showStoredValue")
    assert "textContent" in body
    assert "innerHTML" not in body


def test_an_empty_value_is_never_given_an_invented_option():
    """"" is the state the markup already has an option for. Manufacturing one
    would produce an option labelled " — nėra sąraše", which says nothing."""
    body = _fn("showStoredValue")
    assert "v !== ''" in body


# --------------------------------------------------------------- saving it back
def test_collect_sends_the_select_value_unchanged():
    """No `||` on the source: a fallback here is the same invention one step
    later. The municipality keeps its `|| null` because its column is nullable
    and "" is how a <select> spells NULL."""
    body = _fn("collect")
    assert "source: $('dSource').value," in body
    assert "municipality: $('dMuni').value || null," in body


def test_paste_will_not_guess_the_source_either():
    """api.paste routes the parser on the declared source and only sniffs the
    text when there is none, and sniffing an auction notice pasted without its
    URL reads the market valuation where the auction parser reads the starting
    price. The field used to arrive pre-set to a portal, which answered both
    "where did this come from" and "which parser" by guessing. It now asks."""
    handler = APP_JS.split("$('btnPaste').onclick", 1)[1].split("$('btnSave')", 1)[0]
    guard = handler.index("!$('dSource').value")
    assert guard < handler.index("api('/paste'")


# ---------------------------------------------------- what did not need changing
def test_no_new_control_and_no_new_table_cell_were_added():
    """Two of the standing frontend checks do not apply to this change and are
    noted rather than faked. No new interactive control: #dSource and #dMuni
    already existed and are already sized by `.field input,.field select` under
    pointer:coarse, re-asserted here rather than skipped. No new table cell
    either — the source moved inside the existing Vietovė cell, whose
    data-label test_frontend_responsive.py already pins to its <th>."""
    block = CSS.split("@media (pointer:coarse){", 1)[1]
    assert ".field input,.field select{min-height:44px}" in block
    assert INDEX_HTML.count('<select id="dSource">') == 1
    # The provenance line is still inside the cell labelled Vietovė — no new
    # cell, so nothing new for a data-label to drift out of sync with.
    cell = _fn("loadCandidates").split("provenance", 1)[1].split("));", 1)[0]
    assert "'Vietovė'" in cell


def test_the_row_list_names_the_missing_source_too():
    """The list cell had the same hole in a milder form: an empty source left
    "municipality · " with a separator and nothing after it, which reads as a
    rendering gap rather than as a fact about the row."""
    body = _fn("loadCandidates")
    assert "c.source || 'šaltinis nenurodytas'" in body
