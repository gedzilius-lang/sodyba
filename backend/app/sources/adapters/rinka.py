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


def list_url(category: str, page: int = 1, per_page: int = 200) -> str:
    try:
        path = CATEGORIES[category]
    except KeyError:
        raise UnknownCategory(category) from None
    return f"{BASE}{path}?page={page}&per_page={per_page}"


def _newest_block_start(html: str) -> int | None:
    """Offset where the site-wide newest-adverts block opens, or None."""
    text = html or ""
    for m in _ADS_BLOCK_RE.finditer(text):
        if _CARDS_CLASS_RE.search(m.group(0)):
            continue                       # the results container itself
        if _HEADING_AFTER_RE.match(text, m.end()):
            return m.start()
    return None


def results_bounded(html: str) -> bool:
    """Whether this page's own results could be told from the newest block.

    False means the page shape changed and list_ids fell back to returning
    every id it found — the pre-2026-08-13 behaviour, which mislabels the
    newest block's category but loses nothing. PURE: the caller decides what
    to say about it, the way main.py reports registry.stale().
    """
    return _newest_block_start(html) is not None


def is_list_page(html: str) -> bool:
    """Whether this response was one of the site's list pages at all.

    The poller needs this apart from list_ids(): once the newest block is
    excluded, a page past the end of a category yields zero ids legitimately,
    and that must not be confused with a rate limiter or maintenance notice
    wearing a 200. Either landmark is enough — any listing link at all, or
    the newest block — so a redesign that moves one still reads as a page.
    """
    return bool(_LINK_RE.search(html or "")) or _newest_block_start(html) is not None


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

    d["source"] = KEY
    d["url"] = url
    d["raw"] = body[:4000]
    return d
