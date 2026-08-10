"""Identity across ingest paths. PURE.

The same sodyba legitimately arrives three times once polling, alerts and paste
all feed the same table. URLs differ per portal, so identity has to come from
the property itself. A cadastral number settles it outright; otherwise it is
municipality plus locality plus agreeing numbers plus overlapping title words.

Tolerances are loose on price (portals lag each other, and sellers cut) and
tight on municipality and locality (never guessed, and cheap to compare).

Title words are compared after stripping the listing's own place names, not
just a fixed stop-list: municipality is already an exact-match gate by the
time titles are compared, so district words appear in both titles by
construction and would only inflate similarity, never distinguish two
different villages in the same district.
"""
from __future__ import annotations
import hashlib
import re
from typing import Any

PRICE_TOL = 0.05      # 5% — covers a price cut between two portals' snapshots
AREA_TOL = 0.05
PLOT_TOL = 0.10
TITLE_OVERLAP = 0.40  # Jaccard over words of 4+ characters, place words stripped

_WORD_RE = re.compile(r"[0-9a-ząčęėįšųūž]{4,}", re.I)
_STOP = {"parduodama", "parduodamas", "parduodu", "sodyba", "sodybą", "sodybos",
         "namas", "namą", "skelbimas", "rajone", "rajono",
         "prie", "netoli", "šalia", "su", "apie"}

# Lithuanian declines heavily: the same feature appears as "ežero" on one
# portal and "ežeras" on another. Comparing whole words scores those at zero.
# Four characters is the stem length filters.py already matches on
# (WATER_WORDS = ["ežer", ...]), so the two modules agree about what a
# keyword is.
STEM_LEN = 4


def _stem(word: str) -> str:
    return word[:STEM_LEN]


def fingerprint(listing: dict[str, Any]) -> str:
    """Stable identity for one source's own re-sends. Query strings ignored."""
    if listing.get("url"):
        base = re.sub(r"[?#].*$", "", listing["url"])
    else:
        base = "|".join(str(listing.get(k) or "") for k in
                        ("source", "municipality", "locality", "price_eur", "house_m2"))
    return hashlib.sha1(base.encode()).hexdigest()[:16]


def title_tokens(title: str | None, *place: str | None) -> set[str]:
    """Words worth comparing: generic property vocabulary and the listing's
    own place names are removed. Municipality is already an exact-match gate,
    so district words appear in both titles by construction and only inflate
    the score. Words are stemmed to STEM_LEN so that Lithuanian declension
    (ežero/ežeras, pirtimi/pirtis) does not defeat the comparison — all three
    sides (title words, stop words, place words) must be stemmed, or the
    subtraction stops working."""
    noise = {_stem(w) for w in _STOP}
    for p in place:
        noise |= {_stem(w.lower()) for w in _WORD_RE.findall(p or "")}
    return {_stem(w.lower()) for w in _WORD_RE.findall(title or "")} - noise


def _close(a: Any, b: Any, tol: float) -> bool:
    """True when both are absent, or both present and within tol of each other."""
    if a is None or b is None:
        return a is None and b is None
    a, b = float(a), float(b)
    if a == 0 and b == 0:
        return True
    return abs(a - b) <= max(abs(a), abs(b)) * tol


def is_duplicate(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ca, cb = a.get("cadastral_no"), b.get("cadastral_no")
    if ca and cb:
        return str(ca).strip() == str(cb).strip()

    if (a.get("municipality") or "") != (b.get("municipality") or ""):
        return False

    la, lb = (a.get("locality") or "").strip(), (b.get("locality") or "").strip()
    if la and lb and la != lb:
        return False          # different villages: never the same property

    if not _close(a.get("price_eur"), b.get("price_eur"), PRICE_TOL):
        return False
    if not _close(a.get("house_m2"), b.get("house_m2"), AREA_TOL):
        return False
    if not _close(a.get("plot_ares"), b.get("plot_ares"), PLOT_TOL):
        return False

    ta = title_tokens(a.get("title"), a.get("municipality"), a.get("locality"))
    tb = title_tokens(b.get("title"), b.get("municipality"), b.get("locality"))
    if not ta or not tb:
        # No usable title on one side. Only merge when the place agrees exactly;
        # absent locality is not evidence, and a missed duplicate merely shows a
        # row twice while a false merge hides a property forever.
        return bool(la and lb and la == lb)
    overlap = len(ta & tb) / len(ta | tb)
    return overlap >= TITLE_OVERLAP


def find_duplicate(listing: dict[str, Any],
                   rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for r in rows:
        if is_duplicate(listing, r):
            return r
    return None
