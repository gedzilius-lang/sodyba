"""rinka.lt extraction. PURE -- no I/O.

robots.txt is `User-agent: * / Disallow:` -- fully open. Verified 2026-08-10 and
recorded in sources/registry.py.

Structure verified against a live listing the same day:
  * the host serves several property categories, each at its own path under
    /nekilnojamojo-turto-skelbimai/ -- see CATEGORIES. robots.txt is a
    property of the host, not of a path, so they share one registry entry and
    one crawl delay, and the category is named per call to list_url();
  * listing URLs are /skelbimas/<slug>-id-<N>, N numeric and descending, which
    is why the poller only needs a high-water mark per category;
  * price renders as `Kaina: 60000,00 &euro;` inside <span class="price">;
  * the page nav lists every municipality in the country, so municipality is
    taken from the <h1> and the content block, never from the whole document.

Fix round 1: a live page measured nav at 20% of the document, <h1> at 35%,
the plot size at 42%, and the price block at 47%. Slicing from the price
block (the original boundary) discarded the description -- and the plot
size with it -- whenever the description sits before the price, which is
the common ordering on real listings. The heading is the earliest boundary
that still excludes the nav, so _content() now cuts there, falling back to
the price block only if no <h1> is found at all.

Fix round 2: below the listing, the page renders the same seller's other
adverts under a heading ("Visi vartotojo skelbimai") -- each with its own
URL, description, price and place name. Cutting only at the end of the
document let LOCALITY_RE.search's first-match win on a neighbouring
advert's village whenever the real listing's own description named none,
producing a confident village + lake distance for a property that may be
hundreds of km away. Matching the Lithuanian heading text would be a
narrower version of the same fragility this file already avoids elsewhere
(see the nav dropdown above) -- so _content() instead bounds the slice
structurally, at the first link to a *different* listing id, using the id
already carried in the URL every caller passes in.

Fix round 3: the same class of bug on the LIST page. Every list page renders
a second block of adverts below the results -- the site's ~10 newest, the
same ones on every page of every category, and of any property type. Because
list_ids read the whole document, those entered the pipeline as results of
whichever category was being polled: an apartment could arrive as a sodyba,
and source_category recorded a category the listing never came from. The
block is now excluded, bounded structurally the way _content() bounds the
detail page -- see _ADS_BLOCK_RE for the markup, the two signals used, and
the measurements. A page whose block cannot be located is still read whole:
a wrong category label is bad, a listing that never arrives is worse.
"""
from __future__ import annotations
import re
from typing import Any

from .. import parsers

KEY = "rinka"
BASE = "https://www.rinka.lt"
# One host, one robots.txt, one crawl delay -- so one registry entry (see
# sources/registry.py). Categories are a path concern and live here, which is
# why adding one costs nothing and cannot weaken the policy check.
CATEGORIES = {
    "sodybos": "/nekilnojamojo-turto-skelbimai/parduodamos-sodybos",
    "namai": "/nekilnojamojo-turto-skelbimai/parduodami-namai",
}


class UnknownCategory(KeyError):
    """A category key this adapter does not declare."""

_LINK_RE = re.compile(r'href="(https://www\.rinka\.lt/skelbimas/[^"?#]*?-id-(\d+))"')

# Every list page renders TWO blocks of adverts, and both use id="adsBlock"
# (the markup is invalid that way, but it is what the site serves):
#
#   <div id="adsBlock" class="cards clearfix">   this category's results
#       <div class="ad" ...>
#   <div id="adsBlock">                          the site's newest adverts
#       <h2>Naujausi skelbimai</h2>
#       <div class="ad" ...>
#
# The second block is the same ~10 site-wide newest adverts on every page of
# every category, and they can be any property type at all — measured
# 2026-08-12, the first entry on parduodamos-sodybos page 1 was a 215,000 EUR
# butas. Reading them as category results is how an apartment reached the
# sodyba candidate pool, and how source_category came to record a category a
# listing never came from.
#
# The boundary is taken structurally, twice over, for the same reason
# _content() refuses to match "Visi vartotojo skelbimai" on the detail page:
# a Lithuanian heading is a translation or a copy edit away from breaking.
# The newest block is the adsBlock opener that (a) does not carry the results
# container's `cards` class and (b) opens with a heading element rather than
# an advert. Either signal alone would be enough today; together they survive
# the class being renamed (b still fires) and a heading being added inside
# the results container (a still excludes it).
#
# Measured 2026-08-12 at per_page=200:
#   sodybos page 1  96 links -> 86 results + 10 newest
#   namai   page 1 208 links -> 200 results + 10 newest, 2 of which are
#                               genuinely in namai and stay, because the
#                               cut is positional and not by id
#   any page past the end of a category: 10 links -> 0 results
_ADS_BLOCK_RE = re.compile(r'(?is)<div[^>]*\bid="adsBlock"[^>]*>')
_CARDS_CLASS_RE = re.compile(r'(?i)\bclass="[^"]*\bcards\b')
_HEADING_AFTER_RE = re.compile(r"(?is)\s*<h[1-6]\b")

# The category-results controller's own furniture: the block carrying the
# result count ("Rasta 86 skelbimų") and the pagination widget beside it.
#
# This is what tells a real category page from the site answering with
# something else inside the same chrome, and it has to be a POSITIVE signal.
# The newest-adverts block above is site-wide chrome -- measured 2026-08-13,
# a request for a category path that does not exist renders the full layout,
# newest-adverts block included, with no results furniture at all. So
# "the page had some listing markup" cannot mean "the category answered":
# that reading makes an error page look like an empty category, which lets
# the walk end early and the watermark advance over pages never fetched.
#
# Either element is accepted. Both were present on every real category page
# measured -- results pages and pages past the end of a category alike, since
# the same controller renders both -- and neither was present on the error
# page, so requiring only one survives a rename without weakening the test.
# No Lithuanian text is matched: the class names alone separate the cases,
# so the "Rasta"/"Puslapis" wording stays out of it, as the nav dropdown and
# the seller-adverts heading do elsewhere in this file.
_RESULTS_CONTROL_RE = re.compile(r'(?i)<div[^>]*\bclass="[^"]*\blistControlBlock\b')
_PAGGING_RE = re.compile(r'(?i)<[a-z]+[^>]*\bclass="[^"]*\bpagging\b')
_H1_RE = re.compile(r"(?is)<h1[^>]*>(.*?)</h1>")
_H1_OPEN_RE = re.compile(r"(?i)<h1\b")
_CONTENT_MARK = 'class="price"'
# Any link to another listing, relative or absolute -- the seller's other
# adverts block is what _content() truncates at, so this must not assume
# the host prefix the way _LINK_RE (built for the category page) does.
_OTHER_LISTING_RE = re.compile(r'href="[^"]*?/skelbimas/[^"?#]*?-id-(\d+)"')
_URL_ID_RE = re.compile(r"-id-(\d+)(?:[/?#]|$)")
# A punctuation run left behind when nested markup (an icon <span>, etc.)
# flattens to nothing, e.g. "sodyba, . Vienkemis" -- collapse it to
# "sodyba. Vienkemis" rather than shipping the stray ", .".
_PUNCT_RUN_RE = re.compile(r"[.,]\s+(?=[.,])")

# Labelled structured fields, present and populated on every one of 20 live
# listings measured 2026-08-10 -- unlike free-text extraction, which located
# a village in only 4/20 (most Lithuanian village names simply don't end in
# the "...k." shape LOCALITY_RE looks for). The label is the site's own
# authoritative value, so it is tried first; the heading/content-block
# regexes remain the fallback for the rare page missing it. The value sits
# on the line after the label once HTML flattens ("Miestas / Rajonas:\n
# Plungės r. sav."), so \s* (which matches the newline) bridges the two.
_LABEL_MUNI_RE = re.compile(r"Miestas\s*/\s*Rajonas\s*:\s*(.+)")
_LABEL_LOC_RE = re.compile(r"Mikrorajonas\s*/\s*Gyvenvietė\s*:\s*(.+)")

# ------------------------------------------------------------- self-identity
# Every real advert page declares which advert it is, at document level, twice
# over:
#
#     <meta name="advertisement-id" content="4992805" />
#     <meta property="og:url" content=".../skelbimas/<slug>-id-4992805"/>
#
# Measured 2026-08-13 against the live site: both present on ids 4992805 and
# 4924114 (and on the page saved 2026-08-10 as rinka_detail_live.html, so the
# landmark is not new); NEITHER present on any of three pages that are not
# adverts — the 404 body served for a missing id, the site root, and a category
# path that does not exist.
#
# This is the only honest way to tell "the advert we asked for" from "whatever
# the site served instead", and nothing in the parsed payload can stand in for
# it. Those three non-advert pages all render the site-wide newest-adverts
# block, and reading one of them whole yields price 215000 EUR, 104.42 m2,
# 25.13 a and a municipality — a FULLER row than the genuine listing at
# 4992805, which carries no price and no floor area at all. So any rule that
# counts populated fields ranks the error page above the listing, and the rule
# that used to live in poller._poll_category ("no price and no floor area
# means this is not a listing") did exactly that: it admitted the error page
# and refused the homestead.
#
# Both metas sit in <head>, one per document. That is what makes them safe:
# `data-advertisement-id` is on the page too, but once per advert CARD, so on
# a page carrying the newest-adverts block it hands back a stranger's id.
_META_TAG_RE = re.compile(r"(?is)<meta\b[^>]*>")
_META_AD_ID_RE = re.compile(r'(?i)\bname\s*=\s*"advertisement-id"')
_META_OG_URL_RE = re.compile(r'(?i)\bproperty\s*=\s*"og:url"')
_META_CONTENT_RE = re.compile(r'(?i)\bcontent\s*=\s*"([^"]*)"')

# ---------------------------------------------------------------- the price
# rinka renders the asking price inside <span class="price"> — "Kaina:
# 60000,00 &euro;". That element is the page's own authoritative value, so it
# is read first and free text is only the fallback: the same
# labelled-field-before-free-text precedence municipality, locality and the
# phone number already follow in this file.
#
# It HAS to be read structurally, because parsers.PRICE_RE cannot see a price
# below 100 EUR at all — its bare digit run is \d{3,8}, a floor inherited from
# a pattern built to find thousands-separated prices in running prose. That
# floor is an accident, not a rule: nothing anywhere states that a small number
# is an implausible price, and relying on it is dangerous precisely because it
# looks like it works. Widen PRICE_RE to \d{1,8} some day — an entirely
# reasonable-looking change — and every advert printing "Kaina: 1,00 EUR"
# starts storing price_eur = 1.0. That is the worst outcome on offer here:
# mailbox._insert copies the price into costs_json["purchase"], and the whole
# project ranks on EUR per score point, so a placeholder would sit at the top
# of the list as the cheapest homestead in Lithuania.
#
# So the rule is stated rather than inherited. Measured 2026-08-13, ids
# 4992805 and 4924114 both print "Kaina: 1,00 &euro;" — the nominal placeholder
# some sellers use instead of naming a price. A euro is not an asking price for
# a house, and None (unknown) is the honest reading: filters already treat an
# unknown as a reject rather than a match, and scoring leaves the purchase cost
# at the operator's own default.
#
# Nothing is dropped silently. `raw` carries the page's own text, and
# mailbox._insert stores it as the candidate's `notes`, so "Kaina: 1,00 €" is
# on the row for the operator to read.
NOMINAL_PRICE_MAX_EUR = 100.0
_PRICE_SPAN_RE = re.compile(
    r'(?is)<span[^>]*\bclass="[^"]*\bprice\b[^"]*"[^>]*>(.*?)</span>')
#
# NBSP and the euro sign are written as escapes so this line stays pure
# ASCII and cannot be mangled by an editor's encoding, the same rule
# parsers.PRICE_RE follows for the same reason.
_PRICE_VALUE_RE = re.compile(
    r"(\d[\d  .]*?)(?:,(\d{1,2}))?\s*(?:€|EUR)", re.I)

# The date the advert went online sits in the <div class="infoBlock"> beside
# the location, each field introduced by a material-icons glyph:
#     <i ...>&#xE55F;</i> Prienų r. |  <i ...>&#xE878;</i> 2021 07 05 |
#
# It MUST be keyed off that structure. The same page carries a second date in
# the seller panel -- "Nuo 2021 07 05" under the seller's name -- which is when
# the *member* joined rinka.lt, not when this advert was posted. On the saved
# Prienai page the two happen to be identical, so a loose date regex looks
# correct there and silently reports an account's age as an advert's age on
# every other listing. infoBlock is a structural boundary the seller panel is
# not inside, so keying on it cannot make that mistake.
#
# The div carries no nested <div>, on either of the page's two infoBlocks (the
# listing header and the gallery modal), so the non-greedy cut at </div> takes
# the whole block and nothing after it.
_INFO_BLOCK_RE = re.compile(
    r'(?is)<div[^>]*\bclass="[^"]*\binfoBlock\b[^"]*"[^>]*>(.*?)</div>')
_INFO_DATE_RE = re.compile(r"\b(\d{4})[ ./-](\d{1,2})[ ./-](\d{1,2})\b")

# Contact details. The page shows the phone truncated behind a "rodyti visą"
# reveal, but the full number is in the markup four times over: the reveal
# button's own data-number attribute and the mobile tel:/sms: links. Those are
# the site's own structured value, so they are read first and the flattened
# description text ("teirautis tel.867132403") is only the fallback -- the same
# labelled-field-before-free-text precedence the location fields use above.
_DATA_NUMBER_RE = re.compile(r'(?i)data-number="([^"]+)"')
_TEL_HREF_RE = re.compile(r'(?i)href="(?:tel|sms):([^"]+)"')


def _label_value(m: re.Match | None) -> str | None:
    """The text captured after a labelled field, or None for absent/'-'.

    rinka.lt renders an empty field as a bare '-' rather than omitting the
    label entirely -- treat that the same as no label at all, not as a
    place literally named "-".
    """
    if not m:
        return None
    v = m.group(1).strip()
    return v if v and v != "-" else None


def declared_listing_id(html: str) -> int | None:
    """The advert id this page declares itself to be, or None. PURE.

    None means "this document does not say it is an advert" — a 404 body, the
    site root, an error page inside the site chrome — and the poller refuses
    to ingest such a page as the listing it asked for. See the _META_* block
    above for the two landmarks, the measurements, and why no rule over the
    parsed payload can answer this question.

    The site's own `advertisement-id` wins over og:url when both are present:
    it is the value rinka states about the advert, while og:url is a link
    whose id has to be re-derived from a slug.
    """
    og: int | None = None
    for tag in _META_TAG_RE.finditer(html or ""):
        text = tag.group(0)
        content = _META_CONTENT_RE.search(text)
        if not content:
            continue
        if _META_AD_ID_RE.search(text):
            value = content.group(1).strip()
            if value.isdigit():
                return int(value)
        elif og is None and _META_OG_URL_RE.search(text):
            og = _listing_id(content.group(1))
    return og


def _price_value(text: str) -> float | None:
    """A euro amount written the way rinka writes it, or None. PURE.

    Handles the thousands separators the site uses (space, NBSP or full
    stop) and the two-decimal tail it always prints, so "60 000,00 EUR",
    "60.000 EUR" and "1,00 EUR" all read correctly. Unlike
    parsers.PRICE_RE it has no minimum digit count: telling a nominal
    placeholder from a real asking price is a decision, taken once and
    named in _price(), not a side effect of what a regex happens to see.
    """
    m = _PRICE_VALUE_RE.search(text or "")
    if not m:
        return None
    whole = re.sub(r"\D", "", m.group(1))
    if not whole:
        return None
    return float(f"{whole}.{m.group(2)}" if m.group(2) else whole)


def _price(content_html: str, body: str) -> float | None:
    """The asking price in EUR, or None when the page names none. PURE.

    Reads the site's own <span class="price"> first and falls back to free
    text only when the page carries no price element at all. A price element
    that is PRESENT is authoritative even when nothing parses out of it: a
    number scraped from the description instead would be a guess dressed as
    the seller's asking price, exactly as an unrecognised "Miestas / Rajonas"
    label yields None rather than falling through to a free-text municipality.

    A value below NOMINAL_PRICE_MAX_EUR is a placeholder, not a price, and
    comes back None — unknown. See the NOMINAL_PRICE_MAX_EUR block above.
    """
    span = _PRICE_SPAN_RE.search(content_html or "")
    if span:
        value = _price_value(parsers.to_text(span.group(1)))
    else:
        value = parsers._f(parsers.PRICE_RE.search(body))
    if value is None or value < NOMINAL_PRICE_MAX_EUR:
        return None
    return value


def list_url(category: str, page: int = 1, per_page: int = 200) -> str:
    try:
        path = CATEGORIES[category]
    except KeyError:
        raise UnknownCategory(category) from None
    return f"{BASE}{path}?page={page}&per_page={per_page}"


def _newest_block_start(html: str) -> int | None:
    """Offset where the site-wide newest-adverts block opens, or None.

    None also means "do not cut": see the layout check at the end. Cutting on
    a page laid out differently from every one measured would silently yield
    zero listings, and a silent zero is the failure this file exists to
    avoid, so the ambiguous case falls back to reading the whole page.
    """
    text = html or ""
    start = None
    for m in _ADS_BLOCK_RE.finditer(text):
        if _CARDS_CLASS_RE.search(m.group(0)):
            continue                       # the results container itself
        if _HEADING_AFTER_RE.match(text, m.end()):
            start = m.start()
            break                          # the FIRST one: everything after
            #                                it is the block and whatever
            #                                follows the block, never results
    if start is None:
        return None
    for m in _ADS_BLOCK_RE.finditer(text):
        if _CARDS_CLASS_RE.search(m.group(0)) and m.start() > start:
            # The results container opens BELOW the newest block. Every page
            # measured has it above, so this layout is one we do not
            # understand -- cutting here would discard the results entirely
            # and report a healthy empty category forever.
            return None
    return start


def results_bounded(html: str) -> bool:
    """Whether this page's own results could be told from the newest block.

    False means the page shape changed and list_ids fell back to returning
    every id it found — the pre-2026-08-13 behaviour, which mislabels the
    newest block's category but loses nothing. PURE: the caller decides what
    to say about it, the way main.py reports registry.stale().
    """
    return _newest_block_start(html) is not None


def is_category_page(html: str) -> bool:
    """Whether the site's category-results controller rendered this page.

    The poller needs this apart from list_ids(): once the newest-adverts
    block is excluded, a page past the end of a category yields zero ids
    legitimately, and that must be told apart from the site answering with
    something else inside the same chrome. Only a page this returns True for
    may end the walk on zero results; anything else stalls it.

    Deliberately NOT "does the page contain listing markup". The newest
    block is chrome and renders on error pages too — see _RESULTS_CONTROL_RE.
    """
    text = html or ""
    return bool(_RESULTS_CONTROL_RE.search(text) or _PAGGING_RE.search(text))


def list_ids(html: str) -> list[tuple[int, str]]:
    """This category's own listing ids and urls, newest first, deduplicated.

    The site-wide newest-adverts block that every page carries is excluded —
    see _ADS_BLOCK_RE above for why and for the measurements. If that block
    cannot be located the whole page is read as before: a listing filed under
    the wrong category is bad, a listing that never arrives is worse.
    """
    text = html or ""
    cut = _newest_block_start(text)
    if cut is not None:
        text = text[:cut]
    seen: dict[int, str] = {}
    for url, num in _LINK_RE.findall(text):
        seen.setdefault(int(num), url)
    return sorted(seen.items(), key=lambda kv: -kv[0])


def _listing_id(url: str) -> int | None:
    """The numeric id trailing a rinka.lt listing URL, or None.

    Used to tell the current listing apart from links to other listings
    (the seller's other adverts block) inside the same page.
    """
    m = _URL_ID_RE.search(url or "")
    return int(m.group(1)) if m else None


def _content(html: str, current_id: int | None) -> str:
    """The current listing's own markup: from the heading up to, but not
    including, the first link to a *different* listing.

    The page navigation lists every municipality in Lithuania, so extraction
    over the whole document picks the first dropdown entry. The heading is
    the earliest reliable boundary that excludes it -- measured on a live
    page: nav at 20% of the document, <h1> at 35%, the plot size at 42%,
    the price block at 47%. Cutting at the price block discards the
    description, and with it the plot size.

    Below the listing the page also renders the same seller's other
    adverts, each with its own place name -- and LOCALITY_RE.search takes
    the first match, so an unbounded slice can hand a neighbouring advert's
    village to this listing. Truncating at the first other-listing link is
    a structural boundary (an id, not a heading phrase to translate) and
    holds even if that block's wording changes.
    """
    text = html or ""
    m = _H1_OPEN_RE.search(text)
    start = m.start() if m else text.find(_CONTENT_MARK)
    slice_ = text[start:] if start >= 0 else text
    if current_id is not None:
        for link in _OTHER_LISTING_RE.finditer(slice_):
            if int(link.group(1)) != current_id:
                return slice_[:link.start()]
    return slice_


def _listed_at(content_html: str) -> str | None:
    """The date this advert went online, ISO `YYYY-MM-DD`, or None.

    Read from the infoBlock only (see _INFO_BLOCK_RE) so the seller's "Nuo
    <date>" member-since line can never be mistaken for it. The page writes
    "2021 07 05"; the stored form is "2021-07-05". A block whose date is not a
    plausible calendar date is skipped rather than reformatted into a lie.
    """
    for block in _INFO_BLOCK_RE.finditer(content_html or ""):
        m = _INFO_DATE_RE.search(parsers.to_text(block.group(1)))
        if not m:
            continue
        year, month, day = (int(g) for g in m.groups())
        if not (1990 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31):
            continue
        return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def _contacts(content_html: str, body: str) -> tuple[str | None, str | None]:
    """(phone, email) for this listing, canonical, or None where absent.

    Both are read from the CONTENT SLICE, not the whole document, for the same
    reason locality is (see _content): a page can carry links and panels
    belonging to other adverts, and attaching a stranger's phone number to this
    property would be worse than having none. Bounding it here also keeps the
    page's Google Analytics id ("UA-128041834-1", in a <script> above the
    heading) out of reach.

    No email appeared on any of the 23 live listings measured 2026-08-10; the
    seller panel shows only a "confirmed email" badge with no address. So None
    is the expected answer, and there is deliberately no fallback that would
    manufacture one. Addresses at rinka.lt's own domain are dropped: the
    portal's support address is not the seller's.
    """
    html_ = content_html or ""
    structured = _DATA_NUMBER_RE.findall(html_) + _TEL_HREF_RE.findall(html_)
    phone = next((p for p in (parsers.normalise_phone(v) for v in structured) if p), None)
    if phone is None:
        found = parsers.phones_in(body)
        phone = found[0] if found else None

    host = BASE.split("//", 1)[-1].removeprefix("www.")
    email = next((e for e in parsers.emails_in(body)
                  if (dom := e.rsplit("@", 1)[-1]) != host
                  and not dom.endswith("." + host)), None)
    return phone, email


def parse_detail(html: str, url: str) -> dict[str, Any]:
    content = _content(html, _listing_id(url))
    body = parsers.to_text(content)
    d = parsers._common(body)

    h1 = _H1_RE.search(html or "")
    title = parsers.to_text(h1.group(1)).strip() if h1 else parsers._title(body)
    title = _PUNCT_RUN_RE.sub("", title) if title else title
    d["title"] = (title or "")[:180] or None

    # Municipality: the labelled "Miestas / Rajonas:" field first -- it is
    # the page's own authoritative value, present on 20/20 listings measured,
    # so a present-but-unrecognised label is trusted over a free-text guess
    # and yields None outright rather than falling through to one. Only an
    # ABSENT label (missing, or rendered as '-') falls back to the previous
    # behaviour: heading first, content block second, never the whole page
    # (the nav lists every municipality in the country). Formatting goes
    # through parsers.municipality_from/_label so a city municipality
    # ("Kauno m. sav.") is not relabelled as the district of the same name --
    # they are separate municipalities, and dedupe compares this field exactly.
    label_muni = _label_value(_LABEL_MUNI_RE.search(body))
    if label_muni is not None:
        d["municipality"] = parsers.municipality_from_label(label_muni)
    else:
        d["municipality"] = (parsers.municipality_from(title or "")
                             or parsers.municipality_from(body))

    # Locality: the labelled "Mikrorajonas / Gyvenvietė:" field, passed
    # through unchanged (nominative, e.g. "Plateliai") -- sources.nature.
    # geocode already tolerates the nominative form when resolving a village
    # to coordinates (verified 12/12 against declined gazetteer entries like
    # "Platelių k."), so declining it here would be duplicated, riskier work.
    # Falls back to _common's free-text LOCALITY_RE result, already in
    # d["locality"], when the label is absent.
    label_loc = _label_value(_LABEL_LOC_RE.search(body))
    if label_loc is not None:
        d["locality"] = label_loc

    d["listed_at"] = _listed_at(content)
    d["contact_phone"], d["contact_email"] = _contacts(content, body)
    d["price_eur"] = _price(content, body)

    # Which advert this page says it is -- NOT which advert the caller asked
    # for. The two are compared by poller._is_the_listing, and the whole point
    # is that they can differ: a redirect, a 404 body or an error page inside
    # the site chrome answers with 200 and no identity of its own.
    d["listing_id"] = declared_listing_id(html)

    d["source"] = KEY
    d["url"] = url
    d["raw"] = body[:4000]
    return d
