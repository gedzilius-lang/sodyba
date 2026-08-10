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


def _content(html: str) -> str:
    """Everything from the listing heading onward.

    The page navigation lists every municipality in Lithuania, so extraction
    over the whole document picks the first dropdown entry. The heading is
    the earliest reliable boundary that excludes it -- measured on a live
    page: nav at 20% of the document, <h1> at 35%, the plot size at 42%,
    the price block at 47%. Cutting at the price block discards the
    description, and with it the plot size.
    """
    m = _H1_OPEN_RE.search(html or "")
    if m:
        return html[m.start():]
    i = (html or "").find(_CONTENT_MARK)     # price block, second choice
    return html[i:] if i >= 0 else (html or "")


def parse_detail(html: str, url: str) -> dict[str, Any]:
    body = parsers.to_text(_content(html))
    d = parsers._common(body)

    h1 = _H1_RE.search(html or "")
    title = parsers.to_text(h1.group(1)).strip() if h1 else parsers._title(body)
    title = _PUNCT_RUN_RE.sub("", title) if title else title
    d["title"] = (title or "")[:180] or None

    # Municipality: heading first, content block second. Never the whole page.
    muni = parsers.MUNI_RE.search(title or "") or parsers.MUNI_RE.search(body)
    d["municipality"] = f"{muni.group(1)} rajono" if muni else None

    d["source"] = KEY
    d["url"] = url
    d["raw"] = body[:4000]
    return d
