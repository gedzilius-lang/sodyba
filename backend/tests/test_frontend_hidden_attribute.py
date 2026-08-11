"""Source-text checks that the `hidden` attribute actually hides things.

Same constraint as the other three frontend suites: there is no JS runtime and
no layout engine here (no node, no jsdom, no build step — AGENT.md section 3),
so these assert over source text. What they can prove is a specificity
relationship between two files, which is exactly what went wrong; what they
cannot prove is that the browser agrees. That half was checked by driving Chrome
against the running app and reading the computed style of #drawer before and
after opening a candidate.

The bug this file exists for
---------------------------
`.drawer{display:flex}` sat in styles.css and `hidden` sat on #drawer in
index.html, and the class selector (specificity 0,1,0) outranked the user
agent's `[hidden]{display:none}` (0,0,1 — a bare attribute selector, and in the
UA origin, which loses to the author origin anyway). So the attribute did
nothing. The app opened with an empty "Naujas objektas" form over 45% of a
desktop screen and 100% of a phone, and on a phone the candidate table could
not be tapped at all because the drawer was in front of it.

It was there in the first commit and survived the project's entire life,
because nothing that runs in CI can see a rendered page and nothing that reads
source was looking for it. That is why the test below is written against the
*class* of bug rather than against #drawer: any element that carries `hidden`
in the markup and is given a `display` value by a rule that outranks the UA's
is the same defect, and the next one will be somebody adding `display:grid` to
a panel that happens to be toggled with `hidden`.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
INDEX_HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
STYLES = (ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")

# Comments are stripped before anything is parsed as a rule: this file's own
# prose in styles.css quotes `.drawer{display:flex}` as the cautionary example,
# and a parser that cannot tell a comment from a declaration would report the
# warning as the offence.
NO_COMMENTS = re.sub(r"/\*.*?\*/", " ", STYLES, flags=re.S)
CSS = re.sub(r"\s*([{};:,])\s*", r"\1", re.sub(r"\s+", " ", NO_COMMENTS))

GUARD = "[hidden]{display:none!important}"


def _hidden_elements() -> list[tuple[str, list[str]]]:
    """(tag, [selectors that can match it]) for every element whose start tag
    carries a bare `hidden` attribute. `aria-hidden` and any `data-*hidden` are
    excluded by the lookbehind — they are different attributes with no bearing
    on `display`."""
    found = []
    for tag, attrs in re.findall(r"<(\w+)\b([^>]*)>", INDEX_HTML):
        if not re.search(r"(?<![-\w])hidden(?![\w-])", attrs):
            continue
        selectors = [tag]
        el_id = re.search(r'id="([^"]+)"', attrs)
        if el_id:
            selectors.append(f"#{el_id.group(1)}")
        classes = re.search(r'class="([^"]+)"', attrs)
        if classes:
            selectors += [f".{c}" for c in classes.group(1).split()]
        found.append((tag, selectors))
    return found


def _rules() -> list[tuple[str, str]]:
    """(selector, declarations) for every rule in the stylesheet, including the
    ones nested in @media blocks — the `[^{}]` classes cannot cross a brace, so
    the @media wrapper is skipped over and its contents matched individually."""
    return re.findall(r"([^{}]+)\{([^{}]*)\}", CSS)


def _declares_display(decls: str) -> bool:
    return re.search(r"(^|;)display:", decls) is not None


def _targets(selector: str, ident: str) -> bool:
    """Does this selector name that id, class or tag as a whole token?
    `.drawer` matches `.drawer` and `.drawer.is-on`, not `.drawer-head`."""
    return re.search(rf"(?<![\w.#-]){re.escape(ident)}(?![\w-])", selector) is not None


def _offenders() -> list[tuple[str, str, str]]:
    out = []
    for tag, selectors in _hidden_elements():
        for selector, decls in _rules():
            if not _declares_display(decls):
                continue
            # A tag-name match only counts on its own (`aside{display:flex}`);
            # a descendant like `.grid td` is not a rule about this element.
            hits = [s for s in selectors
                    if _targets(selector, s) and (s.startswith((".", "#"))
                                                  or selector.strip() == s)]
            if hits:
                out.append((tag, selector.strip(), decls.strip()))
    return out


def test_the_hidden_attribute_outranks_every_display_rule():
    """The one test that would have caught C1.

    The guard is asserted unconditionally rather than only when an offender
    exists: without it the stylesheet is one `display:` away from the same bug,
    and the point of a defensive rule is that it is there before the mistake."""
    offenders = _offenders()
    assert GUARD in CSS, (
        "styles.css must carry `[hidden]{display:none!important}`. Without it "
        "the user agent's `[hidden]{display:none}` — a bare attribute selector "
        "— is outranked by any class or id rule that sets `display`, and the "
        "attribute silently stops working. Rules that currently rely on the "
        f"guard: {offenders or 'none, but the next one will not announce itself'}"
    )


def test_no_rule_can_outrank_the_guard_with_important():
    """!important on the guard is beaten by !important at higher specificity.
    Nothing may declare `display` that way on an element that uses `hidden`."""
    beaters = [
        (sel.strip(), decls.strip())
        for tag, selectors in _hidden_elements()
        for sel, decls in _rules()
        if re.search(r"(^|;)display:[^;]*!important", decls)
        and sel.strip() != "[hidden]"
        and any(_targets(sel, s) for s in selectors if s.startswith((".", "#")))
    ]
    assert not beaters, (
        f"these would win over `{GUARD}` and re-break the attribute: {beaters}"
    )


def test_the_drawer_is_the_element_this_was_written_for():
    """Pins the specific case, so the general test above cannot quietly stop
    covering anything. .drawer still needs display:flex when it is *not*
    hidden — the fix was never to delete that rule."""
    assert re.search(r'<aside class="drawer" id="drawer" hidden\b', INDEX_HTML)
    drawer = [d for s, d in _rules() if s.strip() == ".drawer"]
    assert drawer and _declares_display(drawer[0]), (
        "if .drawer no longer sets display, this test is measuring nothing"
    )


def test_the_drawer_is_opened_and_closed_by_the_attribute_not_by_a_style():
    """A `style.display` workaround would sidestep the guard and put the two
    mechanisms back in competition."""
    assert "$('drawer').hidden = false" in APP_JS
    assert "$('drawer').hidden = true" in APP_JS
    assert "drawer').style.display" not in APP_JS


def test_every_element_toggled_by_hidden_in_js_carries_it_in_the_markup():
    """An element revealed by clearing `hidden` must start hidden, or it is
    visible for the first paint — the drawer bug in miniature, once."""
    toggled = set(re.findall(r"\$\('(\w+)'\)\.hidden = ", APP_JS))
    declared = {s[1:] for _, sels in _hidden_elements() for s in sels if s.startswith("#")}
    assert toggled <= declared, (
        f"toggled from JS but not hidden in index.html: {sorted(toggled - declared)}"
    )
