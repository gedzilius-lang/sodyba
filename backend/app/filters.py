"""Filter profiles: the user-selected search presets.

A profile is a saved set of criteria. Incoming listings (from mailbox ingestion)
are tested against every enabled profile; a listing that matches at least one is
kept, tagged with the profiles it hit, and pushed to notifications.

Presets below are starting points derived from the market data: the "elektra +
vanduo" penetration rate differs 4x between municipalities, so the geography in
each preset is chosen, not decorative.
"""
from __future__ import annotations
import re
from typing import Any

# Municipalities ranked by rarity index (share with utilities x share pre-1945),
# computed from the NTR open data.
HIGH_UTILITY = ["Ukmergės rajono", "Utenos rajono", "Anykščių rajono",
                "Molėtų rajono", "Rokiškio rajono"]
LAKE_BELT = ["Ignalinos rajono", "Zarasų rajono", "Molėtų rajono",
             "Švenčionių rajono", "Utenos rajono"]
FOREST_BELT = ["Varėnos rajono", "Lazdijų rajono", "Švenčionių rajono",
               "Šalčininkų rajono", "Trakų rajono"]
CHEAPEST = ["Šalčininkų rajono", "Kelmės rajono", "Zarasų rajono",
            "Ignalinos rajono", "Biržų rajono"]

WATER_WORDS = ["ežer", "upė", "upel", "prie vandens", "pakrant", "tvenkin", "kranto"]
FOREST_WORDS = ["mišk", "giri", "vienkiem", "sodyb"]
UTILITY_WORDS = ["elektra", "gręžin", "šulin", "vandentiek"]
JUNK_WORDS = ["dalis", "1/2", "1/3", "1/4", "be žemės", "sodo bendrij",
              "daugiabut", "butas", "garaž"]

PRESETS: list[dict[str, Any]] = [
    {
        "key": "forest_homestead",
        "name": "Miško vienkiemis",
        "note": "Sodyba miško apsuptyje, su elektra. Dzūkijos ir Aukštaitijos girios.",
        "enabled": True,
        "min_price": 3000, "max_price": 20000,
        "min_plot_ares": 30, "min_house_m2": 40,
        "municipalities": FOREST_BELT + ["Anykščių rajono", "Molėtų rajono"],
        "require_any": FOREST_WORDS,
        "require_all": [],
        "exclude_any": JUNK_WORDS,
        "sources": [],
        "centres": [], "radius_km": None,
        "max_lake_m": None, "max_river_m": 3000, "min_lake_ha": None,
    },
    {
        "key": "lake_shore",
        "name": "Ežero ar upės pakrantė",
        "note": "Prie vandens. Dėmesio: pakrantės apsaugos juostoje statyba ribojama.",
        "enabled": True,
        "min_price": 3000, "max_price": 25000,
        "min_plot_ares": 20, "min_house_m2": 30,
        "municipalities": LAKE_BELT,
        "require_any": [],
        "require_all": [],
        "exclude_any": JUNK_WORDS,
        "sources": [],
        "centres": [], "radius_km": None,
        "max_lake_m": 1500, "max_river_m": None, "min_lake_ha": 5,
    },
    {
        "key": "utilities_first",
        "name": "Su infrastruktūra",
        "note": "Ten, kur elektros ir vandentiekio įvadai realiai egzistuoja (NTR duomenys).",
        "enabled": True,
        "min_price": 3000, "max_price": 20000,
        "min_plot_ares": 15, "min_house_m2": 50,
        "municipalities": HIGH_UTILITY,
        "require_any": UTILITY_WORDS,
        "require_all": [],
        "exclude_any": JUNK_WORDS,
        "sources": [],
        "centres": [], "radius_km": None,
        "max_lake_m": None, "max_river_m": None, "min_lake_ha": None,
    },
    {
        "key": "auction_hunt",
        "name": "Varžytynių medžioklė",
        "note": "Tik varžytynės ir valstybės aukcionai, plati geografija, žema kaina.",
        "enabled": True,
        "min_price": 1000, "max_price": 15000,
        "min_plot_ares": 0, "min_house_m2": 0,
        "municipalities": [],
        "require_any": [],
        "require_all": [],
        "exclude_any": ["butas", "daugiabut", "garaž", "automobil"],
        "sources": ["evarzytynes", "turtas"],
        "centres": [], "radius_km": None,
        "max_lake_m": None, "max_river_m": None, "min_lake_ha": None,
    },
    {
        "key": "bottom_fishing",
        "name": "Pigiausias fondas",
        "note": "Absoliučiai pigiausi rajonai. Blogesnė infrastruktūra, didesnė rizika.",
        "enabled": False,
        "min_price": 1000, "max_price": 10000,
        "min_plot_ares": 20, "min_house_m2": 30,
        "municipalities": CHEAPEST,
        "require_any": [],
        "require_all": [],
        "exclude_any": JUNK_WORDS,
        "sources": [],
        "centres": [], "radius_km": None,
        "max_lake_m": None, "max_river_m": None, "min_lake_ha": None,
    },
]

# Geographic scope. Leave `municipalities` empty and `centres` empty to scan all
# of Lithuania; add centres to draw radius circles around named places instead.
FIELDS = ("key", "name", "note", "enabled", "min_price", "max_price",
          "min_plot_ares", "min_house_m2", "municipalities",
          "require_any", "require_all", "exclude_any", "sources",
          "centres", "radius_km", "max_lake_m", "max_river_m", "min_lake_ha")


def _norm(s: str | None) -> str:
    return (s or "").lower()


def matches(listing: dict[str, Any], profile: dict[str, Any]) -> tuple[bool, str]:
    """Test one listing against one profile. Returns (matched, reason_if_not)."""
    if not profile.get("enabled", True):
        return False, "profilis išjungtas"

    hay = " ".join(filter(None, [
        _norm(listing.get("title")), _norm(listing.get("notes")),
        _norm(listing.get("locality")), _norm(listing.get("municipality")),
    ]))

    src = listing.get("source")
    allowed = profile.get("sources") or []
    if allowed and src not in allowed:
        return False, f"šaltinis {src} ne profilyje"

    price = listing.get("price_eur")
    lo, hi = profile.get("min_price"), profile.get("max_price")
    if price is not None:
        if lo is not None and price < lo:
            return False, f"kaina {price:.0f} < {lo}"
        if hi is not None and price > hi:
            return False, f"kaina {price:.0f} > {hi}"

    munis = profile.get("municipalities") or []
    muni = listing.get("municipality")
    if munis and muni and muni not in munis:
        return False, f"{muni} ne profilio sąraše"

    plot = listing.get("plot_ares")
    if plot is not None and profile.get("min_plot_ares") and plot < profile["min_plot_ares"]:
        return False, f"sklypas {plot} a < {profile['min_plot_ares']} a"

    area = listing.get("house_m2")
    if area is not None and profile.get("min_house_m2") and area < profile["min_house_m2"]:
        return False, f"plotas {area} m2 < {profile['min_house_m2']} m2"

    for w in profile.get("exclude_any") or []:
        if w.lower() in hay:
            return False, f"rastas draudžiamas žodis „{w}“"

    req_all = profile.get("require_all") or []
    for w in req_all:
        if w.lower() not in hay:
            return False, f"trūksta privalomo žodžio „{w}“"

    req_any = profile.get("require_any") or []
    if req_any and not any(w.lower() in hay for w in req_any):
        return False, "nerastas nė vienas raktažodis"

    # --- geographic radius around named centres
    centres = profile.get("centres") or []
    radius = profile.get("radius_km")
    if centres and radius:
        from .sources.nature import geocode
        from .geo import dist_m
        e, n = listing.get("easting"), listing.get("northing")
        if e is None or n is None:
            place = (geocode(listing.get("locality"), listing.get("municipality"))
                     or geocode(listing.get("municipality")))
            if not place:
                return False, "vietos nustatyti nepavyko, o profilis riboja spinduliu"
            e, n = place["easting"], place["northing"]
        best = None
        for c in centres:
            centre = geocode(c)
            if not centre:
                continue
            d = dist_m(e, n, centre["easting"], centre["northing"]) / 1000
            best = d if best is None else min(best, d)
        if best is None:
            return False, "nė vienas profilio centras neatpažintas"
        if best > float(radius):
            return False, f"{best:.0f} km nuo artimiausio centro > {radius} km"

    # --- nature gates, evaluated from measured distances
    nature = listing.get("nature") or {}
    lake, river = nature.get("nearest_lake"), nature.get("nearest_river")
    ml = profile.get("max_lake_m")
    if ml:
        if not lake:
            return False, "ežero nerasta"
        if profile.get("min_lake_ha") and lake["size"] < profile["min_lake_ha"]:
            return False, f"ežeras {lake['size']:.0f} ha < {profile['min_lake_ha']} ha"
        if lake["distance_m"] > ml:
            return False, f"ežeras {lake['distance_m']/1000:.1f} km > {ml/1000:.1f} km"
    mr = profile.get("max_river_m")
    if mr:
        if not river:
            return False, "upės nerasta"
        if river["distance_m"] > mr and not (ml and lake and lake["distance_m"] <= ml):
            return False, f"upė {river['distance_m']/1000:.1f} km > {mr/1000:.1f} km"

    return True, ""


def match_all(listing: dict[str, Any], profiles: list[dict[str, Any]]) -> list[str]:
    """Return the keys of every profile this listing satisfies."""
    return [p["key"] for p in profiles if matches(listing, p)[0]]


def sanitise(raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce a client-supplied profile into the expected shape."""
    out: dict[str, Any] = {}
    for f in FIELDS:
        v = raw.get(f)
        if f in ("municipalities", "require_any", "require_all", "exclude_any",
                 "sources", "centres"):
            out[f] = [str(x).strip() for x in (v or []) if str(x).strip()]
        elif f == "enabled":
            out[f] = bool(v)
        elif f in ("min_price", "max_price", "min_plot_ares", "min_house_m2",
                   "radius_km", "max_lake_m", "max_river_m", "min_lake_ha"):
            out[f] = float(v) if isinstance(v, (int, float)) else None
        else:
            out[f] = str(v or "").strip()
    if not out["key"]:
        out["key"] = re.sub(r"[^a-z0-9_]+", "_", out["name"].lower())[:40] or "profilis"
    return out
