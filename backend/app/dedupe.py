"""Identity across ingest paths. PURE.

The same sodyba legitimately arrives three times once polling, alerts and paste
all feed the same table. URLs differ per portal, so identity has to come from
the property itself. A cadastral number settles it outright; otherwise it is
municipality plus agreeing numbers plus overlapping title words.

Tolerances are loose on price (portals lag each other, and sellers cut) and
tight on municipality (never guessed, and cheap to compare).
"""
from __future__ import annotations
import hashlib
import re
from typing import Any

PRICE_TOL = 0.05      # 5% — covers a price cut between two portals' snapshots
AREA_TOL = 0.05
PLOT_TOL = 0.10
TITLE_OVERLAP = 0.34  # Jaccard over words of 4+ characters

_WORD_RE = re.compile(r"[0-9a-ząčęėįšųūž]{4,}", re.I)
_STOP = {"parduodama", "parduodamas", "parduodu", "sodyba", "sodybą", "sodybos",
         "namas", "namą", "skelbimas", "rajone", "rajono"}


def fingerprint(listing: dict[str, Any]) -> str:
    """Stable identity for one source's own re-sends. Query strings ignored."""
    if listing.get("url"):
        base = re.sub(r"[?#].*$", "", listing["url"])
    else:
        base = "|".join(str(listing.get(k) or "") for k in
                        ("source", "municipality", "locality", "price_eur", "house_m2"))
    return hashlib.sha1(base.encode()).hexdigest()[:16]


def title_tokens(title: str | None) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(title or "")} - _STOP


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
    if not _close(a.get("price_eur"), b.get("price_eur"), PRICE_TOL):
        return False
    if not _close(a.get("house_m2"), b.get("house_m2"), AREA_TOL):
        return False
    if not _close(a.get("plot_ares"), b.get("plot_ares"), PLOT_TOL):
        return False

    ta, tb = title_tokens(a.get("title")), title_tokens(b.get("title"))
    if not ta or not tb:
        return True          # numbers agree and neither title is usable
    overlap = len(ta & tb) / len(ta | tb)
    return overlap >= TITLE_OVERLAP


def find_duplicate(listing: dict[str, Any],
                   rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for r in rows:
        if is_duplicate(listing, r):
            return r
    return None
