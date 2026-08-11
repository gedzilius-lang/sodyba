"""rinka.lt extraction. PURE -- no I/O.

robots.txt is `User-agent: * / Disallow:` -- fully open. Verified 2026-08-10 and
recorded in sources/registry.py.

Structure verified against a live listing the same day:
  * listing URLs are /skelbimas/<slug>-id-<N>, N numeric and descending, which
    is why the poller only needs a high-water mark;
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
"""
from __future__ import annotations
import re
from typing import Any

from .. import parsers

KEY = "rinka"
BASE = "https://www.rinka.lt"
CATEGORY = "/nekilnojamojo-turto-skelbimai/parduodamos-sodybos"

_LINK_RE = re.compile(r'href="(https://www\.rinka\.lt/skelbimas/[^"?#]*?-id-(\d+))"')
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


def list_url(page: int = 1, per_page: int = 200) -> str:
    return f"{BASE}{CATEGORY}?page={page}&per_page={per_page}"


def list_ids(html: str) -> list[tuple[int, str]]:
    """Listing ids and urls, newest first, deduplicated."""
    seen: dict[int, str] = {}
    for url, num in _LINK_RE.findall(html or ""):
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


def parse_detail(html: str, url: str) -> dict[str, Any]:
    body = parsers.to_text(_content(html, _listing_id(url)))
    d = parsers._common(body)

    h1 = _H1_RE.search(html or "")
    title = parsers.to_text(h1.group(1)).strip() if h1 else parsers._title(body)
    title = _PUNCT_RUN_RE.sub("", title) if title else title
    d["title"] = (title or "")[:180] or None

    # Municipality: heading first, content block second. Never the whole page.
    # Formatting goes through parsers.municipality_from so a city municipality
    # ("Kauno m. sav.") is not relabelled as the district of the same name --
    # they are separate municipalities, and dedupe compares this field exactly.
    d["municipality"] = (parsers.municipality_from(title or "")
                         or parsers.municipality_from(body))

    d["source"] = KEY
    d["url"] = url
    d["raw"] = body[:4000]
    return d
