"""Source-text checks over the price cell, the peer figure and the drawer.

Same constraint as the other frontend suites: there is no JS runtime and no
layout engine here (no node, no jsdom, no build step — AGENT.md section 3), so
app.js cannot be executed and styles.css cannot be applied. Everything below
asserts over source text. That is enough to prove a rel attribute is present, a
guard exists, a threshold is named once, and two files still agree about a
label — and it is not enough to prove the cell reads well, that the ochre is
legible on the graphite, or that the button is reachable with a thumb. Those
were checked by driving Chrome at 1440, 1024, 768, 390 and 360px against the
running server with 35 real rinka.lt candidates, and reading the screenshots.

The claim these tests exist to protect is a narrower one than it looks. They
cannot check that a ratio is *right* — test_peer_prices.py does that, against
the server that computes it. What they check is that the screen never shows the
ratio without the two things that make it readable: how many adverts it was
taken against, and on what basis. A ratio rendered bare is a valuation with the
word filed off, and this is a table about prices nobody has paid.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP_JS = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
STYLES = (ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")

CSS = re.sub(r"\s*([{};:,])\s*", r"\1", re.sub(r"\s+", " ", STYLES))


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


def _decls(block: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\{([^{}]*)\}", block)
    assert m, f"no rule for {selector!r}"
    return m.group(1)


def _media(header: str) -> str:
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


def _headers(table_id: str) -> list[str]:
    after = INDEX_HTML.split(f'id="{table_id}"', 1)[1]
    thead = after.split("<thead>", 1)[1].split("</thead>", 1)[0]
    return [re.sub(r"\s+", " ", c).strip()
            for c in re.findall(r"<th[^>]*>(.*?)</th>", thead, re.S)]


def _tag(element_id: str) -> str:
    """The opening tag carrying one id, whitespace collapsed."""
    m = re.search(r"<[^<>]*\bid=\"" + re.escape(element_id) + r"\"[^<>]*>", INDEX_HTML)
    assert m, f"no element with id={element_id!r} in index.html"
    return re.sub(r"\s+", " ", m.group(0))


# ------------------------------------------------- price context, one cell
def test_the_row_renders_the_price_cell_rather_than_a_bare_number():
    assert "td(priceCell(c), 'num', 'Kaina')" in APP_JS
    assert "td(fmt(c.price_eur), 'num', 'Kaina')" not in APP_JS


def test_price_context_added_no_tenth_column():
    """Listing age and the peer ratio are statements about the price, so they
    went into its cell. A tenth column is what clipped VERDIKTAS off the right
    edge before, and that column is the answer the user came for."""
    assert len(_headers("candTable")) == 9
    assert "'Kaina'" in APP_JS, "the price cell lost the card label matching its <th>"


def test_the_price_stays_the_figure_and_the_context_is_secondary():
    """Three numbers of equal weight would be three numbers; the cell works
    only if the price still reads as the price."""
    assert "font-weight:600" in _decls(CSS, ".pricecell .ask")
    ctx = _decls(CSS, ".ctx")
    assert "font-size:10.5px" in ctx
    assert "color:var(--paper-dim)" in ctx


def test_the_listing_age_is_only_rendered_when_the_date_is_known():
    """days_listed is null whenever listed_at is missing or unparseable — a
    pasted candidate has no advert date at all. Nothing is invented for it."""
    body = _fn("priceCell")
    assert "typeof c.days_listed === 'number'" in body
    assert not re.search(r"days_listed\s*\|\|\s*0", body), "no zero standing in for an unknown age"


def test_the_age_is_shown_in_a_unit_that_means_something():
    """"1 863 d." is a number the reader has to divide first. Days survive at
    the short end, where they are the fact rather than arithmetic."""
    body = _fn("ageText")
    assert "d." in body and "mėn." in body and "m." in body


# ------------------------------------------------------------------ staleness
def test_the_stale_threshold_is_named_once_and_is_five_years():
    """Five years, not two: the median advert in the collected set has been
    running about 3.6 years, so a lower threshold would colour three rows in
    four and the accent would stop discriminating. Named once so the choice is
    a single edit rather than a magic number in a template string."""
    m = re.search(r"const DAYS_PER_YEAR = (\d+);", APP_JS)
    assert m, "DAYS_PER_YEAR must be a named constant in app.js"
    assert int(m.group(1)) == 365
    assert "const STALE_DAYS = 5 * DAYS_PER_YEAR;" in APP_JS
    assert "c.days_listed >= STALE_DAYS" in _fn("priceCell")


def test_the_printed_age_and_the_accent_switch_at_the_same_moment():
    """Rounding to nearest printed "5,0 m." for 1 816 days and for 1 837 days —
    one accented, one not, with nothing on screen to tell them apart. The years
    are floored on the same divisor the threshold is expressed in, so "reads
    5,0 m. or more" and "is accented" became the same statement. A colour that
    contradicts the number printed beside it is worse than no colour."""
    assert "Math.floor((days / DAYS_PER_YEAR) * 10) / 10" in _fn("ageText")


def test_staleness_uses_the_palettes_warning_accent_not_its_rejection_colour():
    """Ochre is this console's "look at this" (over-budget tallies, the stale
    robots.txt warning). Oxide means rejected, and a long-running advert is not
    a rejected candidate — it is a priced-wrong one."""
    rule = _decls(CSS, ".ctx.is-stale")
    assert "var(--ochre)" in rule
    assert "oxide" not in rule


def test_staleness_is_a_colour_and_not_a_second_number():
    """The age line is printed on every row that has a date, in the same place,
    so the reader compares rows. Only the colour is rationed."""
    body = _fn("priceCell")
    assert body.count("skelbiama ${ageText(c.days_listed)}") == 1


# ------------------------------------------------------- the peer figure
def test_the_peer_ratio_is_never_rendered_without_its_sample_size():
    """A ratio against n=12 and one against n=29 are different claims. The
    ratio and the count are built in the same expression so one cannot be
    dropped without the other showing up in this diff."""
    body = _fn("priceCell")
    assert "ratioText(f.ratio)" in body
    assert "n=${fmt(f.n)}" in body


def test_the_peer_ratio_states_which_basis_it_used():
    """Every real comparison today falls back to the whole table because no
    municipality has five peers yet; the municipality basis appears as the
    table grows, and the two must be told apart on sight."""
    assert re.search(r"PEER_BASIS = \{ municipality: '[^']+', all: '[^']+' \}", APP_JS)
    assert "PEER_BASIS[f.basis] || f.basis" in _fn("priceCell")


def test_the_peer_ratio_states_which_unit_it_compares():
    """EUR per are of land and EUR per m² of house are different claims. 18 of
    28 real listings carry no floor area, so the column would silently switch
    between them without the unit printed beside the ratio."""
    assert re.search(r"PEER_UNIT = \{ eur_per_are: '[^']+', eur_per_m2: '[^']+' \}", APP_JS)
    assert "PEER_UNIT[metric]" in _fn("priceCell")


def test_the_land_ratio_is_preferred_and_the_house_ratio_is_the_fallback():
    """Order matters and is asserted rather than left to reading order: most
    candidates have a plot size and no floor area, so eur_per_are is the one
    that makes the column comparable down its length."""
    body = _fn("peerFigure")
    assert "['eur_per_are', 'eur_per_m2']" in body


def test_a_missing_peer_comparison_renders_nothing_at_all():
    """Absence is the normal case, not an error: eur_per_m2 is null for 18 of
    28 and the backend answers with a reason rather than a number. A dash in
    its place would be a row of dashes down the column."""
    assert "return [null, null];" in _fn("peerFigure")
    assert "if (f) {" in _fn("priceCell")


def test_the_cell_label_says_asking_prices_rather_than_value():
    """The visible label is the one thing a hurried reader takes from the cell.
    "prašomų" is the whole defence: it says these are prices sellers ask, not
    prices anybody paid."""
    assert "prašomų med." in _fn("priceCell")
    for word in ("vertė", "vertinimas", "rinkos kaina"):
        assert word not in _fn("priceCell"), (
            f"{word!r} in the price cell would read as a valuation"
        )


def test_the_long_form_denies_being_a_valuation_in_as_many_words():
    body = _fn("peerTitle")
    assert "PRAŠOMOS kainos" in body
    assert "nėra turto vertinimas" in body and "ne rinkos vertė" in body


def test_the_note_under_the_table_is_the_servers_own_wording():
    """GET /api/candidates already answers with asking_vs_peers_note, written
    beside the code that computes the ratio. Restating it in the frontend would
    give the screen a second wording free to drift from the first."""
    assert '<p class="note" id="peersNote"></p>' in INDEX_HTML
    assert "$('peersNote').textContent = data.asking_vs_peers_note" in APP_JS


# ---------------------------------------------------- open the advert itself
def test_the_open_ad_anchor_opens_a_new_tab_with_the_opener_severed():
    """rinka.lt is a third-party page. Without rel=noopener the opened tab gets
    window.opener and can navigate this one; noreferrer keeps the console's URL
    out of their logs. Both, on the same anchor, or neither is worth having."""
    tag = _tag("btnOpenAd")
    assert 'target="_blank"' in tag
    assert 'rel="noopener noreferrer"' in tag


def test_the_open_ad_button_is_lithuanian_and_sits_beside_the_url_field():
    block = INDEX_HTML.split('<div class="url-row">', 1)[1].split("</div>", 1)[0]
    assert 'id="dUrl"' in block and 'id="btnOpenAd"' in block
    assert "Atidaryti skelbimą" in INDEX_HTML


def test_a_candidate_with_no_url_gets_no_dead_button():
    """Pasted candidates often have no URL. An <a> without href is not a link —
    it still looks like a button, so it is hidden rather than left inert."""
    body = _fn("syncOpenAd")
    assert "a.hidden = !ok" in body
    assert "a.removeAttribute('href')" in body
    assert 'hidden>' in _tag("btnOpenAd") or "hidden" in _tag("btnOpenAd"), (
        "the markup must start hidden, or an unopened drawer flashes a button "
        "pointing nowhere"
    )


def test_only_an_http_url_ever_reaches_the_href():
    """The URL is third-party text and the field is editable. A javascript:
    URL in an anchor the operator is invited to click is a one-click script
    injection into the console."""
    assert r"/^https?:\/\//i.test(String(url || ''))" in _fn("syncOpenAd")


def test_the_open_ad_button_follows_the_field_it_sits_beside():
    """Set on open, and again on every keystroke — otherwise pasting a URL
    leaves a hidden button until the drawer is closed and reopened."""
    assert "syncOpenAd(CURRENT.url)" in _fn("openDrawer")
    assert "syncOpenAd($('dUrl').value)" in _fn("wire")


def test_the_open_ad_button_is_a_touch_target():
    """.btn carries min-height under pointer:coarse, but min-height does
    nothing to an inline box — an <a class="btn"> left inline would be the one
    control on the screen under 44px."""
    assert "display:inline-flex" in _decls(CSS, ".open-ad")
    assert "44px" in _decls(_media("@media (pointer:coarse)"), ".btn")


# ------------------------------------------------------------------ contacts
def test_the_phone_is_a_tel_link_and_the_email_a_mailto_link():
    """On a handset this is the point of the panel: one tap dials."""
    body = _fn("renderContacts")
    assert 'href="tel:${esc(dial)}"' in body
    assert 'href="mailto:${esc(c.contact_email)}"' in body


def test_the_tel_href_carries_only_dialable_characters():
    assert r"replace(/[^\d+]/g, '')" in _fn("renderContacts")


def test_no_email_row_is_rendered_when_there_is_no_email():
    """None of the 28 real rinka.lt listings carries an email, so a fixed row
    would print an empty labelled "El. paštas" on every candidate."""
    body = _fn("renderContacts")
    assert "if (c.contact_email) {" in body
    assert body.count("El. paštas") == 1, "the email label may exist only inside its guard"


def test_contact_values_are_escaped_before_they_reach_innerhtml():
    """Phone numbers and emails come from a third-party portal like every other
    field on the row."""
    body = _fn("renderContacts")
    assert "esc(c.contact_phone)" in body
    assert "esc(c.contact_email)" in body


def test_contacts_are_rendered_when_the_drawer_opens():
    assert "renderContacts(CURRENT)" in _fn("openDrawer")
    assert '<div id="dContacts"></div>' in INDEX_HTML


def test_an_unsaved_form_shows_no_contacts_block_at_all():
    """"Kontaktų nėra" is a finding about a stored advert. On a blank form it
    would be noise about a candidate that does not exist yet."""
    assert "if (!rows.length && !c.id)" in _fn("renderContacts")


def test_the_contact_links_are_touch_targets_on_a_phone():
    assert "44px" in _decls(_media("@media (pointer:coarse)"), ".natcard a")
