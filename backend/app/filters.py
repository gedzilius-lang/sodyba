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
from dataclasses import dataclass, field as dc_field

HARD, SOFT = "hard", "soft"
MATCH, NEAR, REJECT = "match", "near", "reject"


@dataclass
class Miss:
    field: str
    kind: str                     # HARD | SOFT
    text: str                     # shown in the UI, Lithuanian
    delta: float | None = None    # how far outside, in the field's own unit


@dataclass
class ProfileMatch:
    key: str
    state: str                    # MATCH | NEAR | REJECT
    misses: list[Miss] = dc_field(default_factory=list)

    def as_dict(self) -> dict:
        return {"key": self.key, "state": self.state,
                "misses": [vars(m) for m in self.misses]}

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


def evaluate(listing: dict[str, Any], profile: dict[str, Any]) -> ProfileMatch:
    """Test a listing against a profile, reporting EVERY miss.

    v1 returned on the first failure, which made "5% over the price ceiling"
    indistinguishable from "wrong in six ways" — and discarded both. Soft misses
    are ones a slightly different profile would have accepted; hard misses are
    structural and no profile edit would rescue them.
    """
    key = profile.get("key", "")
    misses: list[Miss] = []

    def hard(fieldname: str, text: str, delta: float | None = None) -> None:
        misses.append(Miss(fieldname, HARD, text, delta))

    def soft(fieldname: str, text: str, delta: float | None = None) -> None:
        misses.append(Miss(fieldname, SOFT, text, delta))

    if not profile.get("enabled", True):
        return ProfileMatch(key, REJECT, [Miss("enabled", HARD, "profilis išjungtas")])

    hay = " ".join(filter(None, [
        _norm(listing.get("title")), _norm(listing.get("notes")),
        _norm(listing.get("locality")), _norm(listing.get("municipality")),
    ]))

    src = listing.get("source")
    allowed = profile.get("sources") or []
    if allowed and src not in allowed:
        hard("sources", f"šaltinis {src} ne profilyje")

    price = listing.get("price_eur")
    lo, hi = profile.get("min_price"), profile.get("max_price")
    if price is not None:
        if lo is not None and price < lo:
            soft("price", f"kaina {price:,.0f} < {lo:,.0f} EUR".replace(",", " "),
                 lo - price)
        if hi is not None and price > hi:
            soft("price", f"kaina {price:,.0f} > {hi:,.0f} EUR".replace(",", " "),
                 price - hi)

    munis = profile.get("municipalities") or []
    muni = listing.get("municipality")
    if munis and muni and muni not in munis:
        soft("municipality", f"{muni} ne profilio sąraše")

    plot = listing.get("plot_ares")
    need_plot = profile.get("min_plot_ares")
    if plot is not None and need_plot and plot < need_plot:
        soft("plot_ares", f"sklypas {plot:.0f} a < {need_plot:.0f} a", need_plot - plot)

    area = listing.get("house_m2")
    need_area = profile.get("min_house_m2")
    if area is not None and need_area and area < need_area:
        soft("house_m2", f"plotas {area:.0f} m2 < {need_area:.0f} m2", need_area - area)

    for w in profile.get("exclude_any") or []:
        if w.lower() in hay:
            hard("exclude_any", f"rastas draudžiamas žodis „{w}“")

    for w in profile.get("require_all") or []:
        if w.lower() not in hay:
            hard("require_all", f"trūksta privalomo žodžio „{w}“")

    misses.extend(_keyword_misses(hay, profile))
    misses.extend(_radius_misses(listing, profile))
    misses.extend(_nature_misses(listing, profile))

    return ProfileMatch(key, _state(misses), misses)


def _state(misses: list[Miss]) -> str:
    """MATCH when clean, REJECT otherwise. Task 6 introduces NEAR here."""
    return MATCH if not misses else REJECT


def _keyword_misses(hay: str, profile: dict[str, Any]) -> list[Miss]:
    words = profile.get("require_any") or []
    if not words:
        return []
    if any(str(w).lower() in hay for w in words):
        return []
    return [Miss("require_any", HARD, "nerastas nė vienas raktažodis")]


def _radius_misses(listing: dict[str, Any], profile: dict[str, Any]) -> list[Miss]:
    centres = profile.get("centres") or []
    radius = profile.get("radius_km")
    if not (centres and radius):
        return []
    from .sources.nature import geocode
    from .geo import dist_m
    e, n = listing.get("easting"), listing.get("northing")
    if e is None or n is None:
        place = (geocode(listing.get("locality"), listing.get("municipality"))
                 or geocode(listing.get("municipality")))
        if not place:
            return [Miss("radius_km", HARD,
                         "vietos nustatyti nepavyko, o profilis riboja spinduliu")]
        e, n = place["easting"], place["northing"]
    best = None
    for c in centres:
        centre = geocode(c)
        if not centre:
            continue
        d = dist_m(e, n, centre["easting"], centre["northing"]) / 1000
        best = d if best is None else min(best, d)
    if best is None:
        return [Miss("radius_km", HARD, "nė vienas profilio centras neatpažintas")]
    if best > float(radius):
        return [Miss("radius_km", SOFT,
                     f"{best:.0f} km nuo artimiausio centro > {radius:.0f} km",
                     best - float(radius))]
    return []


def _nature_misses(listing: dict[str, Any], profile: dict[str, Any]) -> list[Miss]:
    nature = listing.get("nature") or {}
    lake, river = nature.get("nearest_lake"), nature.get("nearest_river")
    out: list[Miss] = []
    ml = profile.get("max_lake_m")
    if ml:
        if not lake:
            out.append(Miss("max_lake_m", SOFT, "ežero nerasta"))
        else:
            need_ha = profile.get("min_lake_ha")
            if need_ha and lake["size"] < need_ha:
                out.append(Miss("min_lake_ha", SOFT,
                                f"ežeras {lake['size']:.0f} ha < {need_ha:.0f} ha",
                                need_ha - lake["size"]))
            if lake["distance_m"] > ml:
                out.append(Miss("max_lake_m", SOFT,
                                f"ežeras {lake['distance_m']/1000:.1f} km "
                                f"> {ml/1000:.1f} km",
                                lake["distance_m"] - ml))
    mr = profile.get("max_river_m")
    if mr:
        lake_ok = bool(ml and lake and lake["distance_m"] <= ml)
        if not river:
            out.append(Miss("max_river_m", SOFT, "upės nerasta"))
        elif river["distance_m"] > mr and not lake_ok:
            out.append(Miss("max_river_m", SOFT,
                            f"upė {river['distance_m']/1000:.1f} km > {mr/1000:.1f} km",
                            river["distance_m"] - mr))
    return out


def evaluate_all(listing: dict[str, Any],
                 profiles: list[dict[str, Any]]) -> list[ProfileMatch]:
    return [evaluate(listing, p) for p in profiles]


def matches(listing: dict[str, Any], profile: dict[str, Any]) -> tuple[bool, str]:
    """v1 signature, kept for api.test_profiles."""
    r = evaluate(listing, profile)
    return r.state == MATCH, (r.misses[0].text if r.misses else "")


def match_all(listing: dict[str, Any], profiles: list[dict[str, Any]]) -> list[str]:
    """Keys of every profile this listing fully satisfies."""
    return [r.key for r in evaluate_all(listing, profiles) if r.state == MATCH]


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
