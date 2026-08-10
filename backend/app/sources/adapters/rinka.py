"""rinka.lt extraction. PURE -- no I/O.

robots.txt is `User-agent: * / Disallow:` -- fully open. Verified 2026-08-10 and
recorded in sources/registry.py.

Structure verified against a live listing the same day:
  * listing URLs are /skelbimas/<slug>-id-<N>, N numeric and descending, which
    is why the poller only needs a high-water mark;
  * price renders as `Kaina: 60000,00 &euro;` inside <span class="price">;
  * the page nav lists every municipality in the country, so municipality is
    taken from the <h1> and the content block, never from the whole document.
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
_CONTENT_MARK = 'class="price"'


def list_url(page: int = 1, per_page: int = 200) -> str:
    return f"{BASE}{CATEGORY}?page={page}&per_page={per_page}"


def list_ids(html: str) -> list[tuple[int, str]]:
    """Listing ids and urls, newest first, deduplicated."""
    seen: dict[int, str] = {}
    for url, num in _LINK_RE.findall(html or ""):
        seen.setdefault(int(num), url)
    return sorted(seen.items(), key=lambda kv: -kv[0])


def _content(html: str) -> str:
    """Everything from the price block onward -- excludes the nav dropdown."""
    i = html.find(_CONTENT_MARK)
    return html[i:] if i >= 0 else html


def parse_detail(html: str, url: str) -> dict[str, Any]:
    body = parsers.to_text(_content(html))
    d = parsers._common(body)

    h1 = _H1_RE.search(html or "")
    title = parsers.to_text(h1.group(1)).strip() if h1 else parsers._title(body)
    d["title"] = (title or "")[:180] or None

    # Municipality: heading first, content block second. Never the whole page.
    muni = parsers.MUNI_RE.search(title or "") or parsers.MUNI_RE.search(body)
    d["municipality"] = f"{muni.group(1)} rajono" if muni else None

    d["source"] = KEY
    d["url"] = url
    d["raw"] = body[:4000]
    return d
