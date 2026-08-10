"""Per-portal extraction from alert emails.

Each portal formats its alerts differently, so each gets its own extractor. All
of them return the same listing dict. The generic fallback handles anything
unrecognised — it still finds a price and an area most of the time, and whatever
it misses you fix in the drawer.

KNOWN GAP: these were tested against alert emails reconstructed from what the
portals publish, never against genuine ones. Expect the first real alerts to land
with a field or two missing. Do not rewrite speculatively — wait for a real alert
from each portal, then tighten against actual text.
"""
from __future__ import annotations
import re
from html import unescape
from typing import Any, Callable

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[ \t\xa0]+")

PRICE_RE = re.compile(r"(\d{1,3}(?:[ .\u00a0]\d{3})+|\d{3,7})\s*(?:EUR|€|Eur)", re.I)
AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:kv\.?\s*m|m2|m²)", re.I)
PLOT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:a\b|ar[ųu]|arai|aro)", re.I)
HA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*ha\b", re.I)
MUNI_RE = re.compile(r"([A-ZĄČĘĖĮŠŲŪŽ][a-ząčęėįšųūž]+)\s*(?:r\.|rajon)", re.U)
CITY_RE = re.compile(r"([A-ZĄČĘĖĮŠŲŪŽ][a-ząčęėįšųūž]+)\s*(?:m\.\s*sav|miesto sav)", re.U)
CAD_RE = re.compile(r"\b(\d{4}[/\-]\d{4}\s*[:\-/]\s*\d{3,5})\b")
LOCALITY_RE = re.compile(r"([A-ZĄČĘĖĮŠŲŪŽ][a-ząčęėįšųūž]+(?:ių|ų|os|ai|iai|ė|as))\s*k\.", re.U)
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
URL_RE = re.compile(r"https?://[^\s\"'<>)]+")


def to_text(body: str) -> str:
    """Flatten an HTML or plaintext email body into scannable text."""
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", body)
    # Surface href targets as plain text so URL extraction survives tag stripping.
    t = re.sub(r'(?i)<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>', r" \1 ", t)
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", t)
    t = TAG_RE.sub(" ", t)
    t = unescape(t)
    t = WS_RE.sub(" ", t)
    return "\n".join(ln.strip() for ln in t.splitlines() if ln.strip())


def _f(m: re.Match | None, idx: int = 1) -> float | None:
    if not m:
        return None
    raw = m.group(idx).replace("\u00a0", "").replace(" ", "")
    if raw.count(".") == 1 and len(raw.split(".")[1]) == 3:
        raw = raw.replace(".", "")          # 17.000 -> 17000
    return float(raw.replace(",", "."))


def _common(text: str) -> dict[str, Any]:
    plot = _f(PLOT_RE.search(text))
    if plot is None:
        ha = _f(HA_RE.search(text))
        plot = ha * 100 if ha is not None else None
    muni = MUNI_RE.search(text) or CITY_RE.search(text)
    loc = LOCALITY_RE.search(text)
    return {
        "price_eur": _f(PRICE_RE.search(text)),
        "house_m2": _f(AREA_RE.search(text)),
        "plot_ares": plot,
        "municipality": f"{muni.group(1)} rajono" if muni else None,
        "locality": f"{loc.group(1)} k." if loc else None,
        "cadastral_no": (lambda m: m.group(1) if m else None)(CAD_RE.search(text)),
    }


def _first_url(text: str, host_hint: str | None = None) -> str | None:
    urls = URL_RE.findall(text)
    if host_hint:
        for u in urls:
            if host_hint in u:
                return u
    for u in urls:
        if not any(x in u for x in ("unsubscribe", "atsisakyti", "mailto",
                                    "facebook", "twitter", ".png", ".jpg", ".gif")):
            return u
    return urls[0] if urls else None


def _title(text: str) -> str | None:
    for ln in text.splitlines():
        if 12 <= len(ln) <= 180 and not ln.lower().startswith(("http", "sveiki", "gerb")):
            return ln[:180]
    return None


def parse_evarzytynes(text: str) -> dict[str, Any]:
    d = _common(text)
    d["source"] = "evarzytynes"
    d["url"] = _first_url(text, "evarzytynes.lt")
    d["title"] = _title(text)
    # Auction notices carry a start price and an end date.
    m = re.search(r"(?:pradin[ėe]\s*(?:pardavimo\s*)?kaina)\D{0,20}"
                  r"(\d{1,3}(?:[ .\u00a0]\d{3})+|\d{3,7})", text, re.I)
    if m:
        d["price_eur"] = _f(m)
    dm = re.search(r"(?:pabaig\w*|iki)\D{0,25}(\d{4}-\d{2}-\d{2})", text, re.I) \
         or DATE_RE.search(text)
    if dm:
        d["auction_ends_at"] = dm.group(1)
    return d


def parse_turtas(text: str) -> dict[str, Any]:
    d = _common(text)
    d["source"] = "turtas"
    d["url"] = _first_url(text, "turtas.lt")
    d["title"] = _title(text)
    m = re.search(r"(?:prad\w+\s*kaina|starto kaina)\D{0,20}"
                  r"(\d{1,3}(?:[ .\u00a0]\d{3})+|\d{3,7})", text, re.I)
    if m:
        d["price_eur"] = _f(m)
    return d


def _classified(source: str, host: str) -> Callable[[str], dict[str, Any]]:
    def fn(text: str) -> dict[str, Any]:
        d = _common(text)
        d["source"] = source
        d["url"] = _first_url(text, host)
        d["title"] = _title(text)
        return d
    return fn


def parse_generic(text: str) -> dict[str, Any]:
    d = _common(text)
    d["source"] = "manual"
    d["url"] = _first_url(text)
    d["title"] = _title(text)
    return d


# sender-domain / subject fragment -> parser
ROUTES: list[tuple[str, Callable[[str], dict[str, Any]]]] = [
    ("evarzytynes.lt", parse_evarzytynes),
    ("registrucentras.lt", parse_evarzytynes),
    ("turtas.lt", parse_turtas),
    ("aruodas.lt", _classified("aruodas", "aruodas.lt")),
    ("domoplius.lt", _classified("domoplius", "domoplius.lt")),
    ("alio.lt", _classified("alio", "alio.lt")),
    ("skelbiu.lt", _classified("skelbiu", "skelbiu.lt")),
    ("rinka.lt", _classified("rinka", "rinka.lt")),
]


def route(sender: str, subject: str, body: str) -> dict[str, Any]:
    """Pick a parser by sender domain, fall back to subject, then generic."""
    text = to_text(body)
    hay = f"{sender} {subject}".lower()
    for needle, fn in ROUTES:
        if needle in hay:
            return fn(text)
    for needle, fn in ROUTES:
        if needle in text.lower():
            return fn(text)
    return parse_generic(text)


def split_listings(text: str) -> list[str]:
    """A digest email may carry several listings. Split on price markers.

    Conservative: only splits when three or more price markers appear, otherwise
    one email is one listing.
    """
    marks = [m.start() for m in PRICE_RE.finditer(text)]
    if len(marks) < 3:
        return [text]
    bounds = [0] + [max(0, p - 320) for p in marks[1:]] + [len(text)]
    out, seen = [], set()
    for a, b in zip(bounds, bounds[1:]):
        chunk = text[a:b].strip()
        if len(chunk) > 60 and chunk[:80] not in seen:
            seen.add(chunk[:80])
            out.append(chunk)
    return out or [text]
