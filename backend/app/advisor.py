"""Nature scoring and written assessment.

Two jobs:

1. `assess_nature` — locate a candidate, measure distance to the nearest lake and
   river, test it against protected-area envelopes, and derive the water and
   forest/water scores from real distances instead of a guess. This is the
   difference between "I think it's near a lake" and "the nearest lake over 5 ha
   is Baluošas, 1.4 km".

2. `advise` — write the counsel. Every finding names its source and its next
   action. Nothing here is a verdict on its own; the register extract and the
   site visit decide, and the text says so.
"""
from __future__ import annotations
from typing import Any

from .sources.nature import geocode, nearest_water, protected_hits

# Distance bands in metres -> score contribution. Nature and water are the
# stated priority, so these are deliberately demanding: 10/10 means you can see
# the water from the plot, not that it is in the same district.
LAKE_BANDS = [(300, 10), (800, 9), (1500, 7), (3000, 5), (6000, 3), (12000, 1)]
RIVER_BANDS = [(300, 9), (800, 8), (1500, 6), (3000, 4), (6000, 2), (12000, 1)]

# A pond under this is a field puddle, not an amenity.
MIN_LAKE_HA = 1.0
MIN_RIVER_KM = 3.0


def _band(distance_m: float | None, bands: list[tuple[int, int]]) -> int:
    if distance_m is None:
        return 0
    for limit, score in bands:
        if distance_m <= limit:
            return score
    return 0


def assess_nature(candidate: dict[str, Any]) -> dict[str, Any]:
    """Locate and measure. Returns {} when the candidate cannot be placed."""
    e, n = candidate.get("easting"), candidate.get("northing")
    matched_place = None
    if e is None or n is None:
        place = (geocode(candidate.get("locality"), candidate.get("municipality"))
                 or geocode(candidate.get("municipality")))
        if not place:
            return {"located": False,
                    "note": "Nepavyko nustatyti vietos — įrašyk gyvenvietę arba koordinates."}
        e, n = place["easting"], place["northing"]
        matched_place = place["name"]

    lake = nearest_water(e, n, "lake", MIN_LAKE_HA)   # `size` is hectares
    river = nearest_water(e, n, "river", MIN_RIVER_KM)
    protected = protected_hits(e, n)

    lake_score = _band(lake["distance_m"] if lake else None, LAKE_BANDS)
    river_score = _band(river["distance_m"] if river else None, RIVER_BANDS)
    water_score = max(lake_score, river_score)
    # Forest/water criterion rewards having both, not just one.
    forest_water = min(10, water_score + (2 if lake_score and river_score else 0))

    return {
        "located": True,
        "easting": round(e, 1), "northing": round(n, 1),
        "matched_place": matched_place,
        "nearest_lake": lake,
        "nearest_river": river,
        "protected_areas": protected,
        "derived_scores": {"water": water_score, "forest_water": forest_water},
        "note": ("Vietovė nustatyta pagal gyvenvietės centroidą, ne pagal sklypą — "
                 "atstumai apytiksliai ±1 km." if matched_place else
                 "Atstumai skaičiuoti nuo įvestų koordinačių."),
    }


def _fmt_km(m: float) -> str:
    return f"{m/1000:.1f} km" if m >= 1000 else f"{m:.0f} m"


def advise(candidate: dict[str, Any], settings: dict[str, Any],
           market: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Produce the written assessment: findings, blockers, and next actions."""
    nature = candidate.get("nature") or {}
    findings: list[dict[str, str]] = []
    blockers: list[str] = []
    actions: list[str] = []

    # ---- water and nature, the stated priority
    lake, river = nature.get("nearest_lake"), nature.get("nearest_river")
    if nature.get("located"):
        if lake:
            findings.append({
                "topic": "Vanduo",
                "text": f"Artimiausias ežeras – {lake['name']}, "
                        f"{_fmt_km(lake['distance_m'])}, ~{lake['size']:.0f} ha.",
                "weight": "good" if lake["distance_m"] <= 1500 else "neutral"})
        if river:
            findings.append({
                "topic": "Vanduo",
                "text": f"Artimiausia upė – {river['name']}, "
                        f"{_fmt_km(river['distance_m'])}, {river['size']:.0f} km ilgio.",
                "weight": "good" if river["distance_m"] <= 1500 else "neutral"})
        if not lake and not river:
            findings.append({"topic": "Vanduo",
                             "text": "Per 12 km nerasta nei ežero, nei reikšmingos upės.",
                             "weight": "bad"})
        near = min([d["distance_m"] for d in (lake, river) if d] or [999999])
        if near <= 200:
            blockers.append(
                "Objektas gali būti pakrantės apsaugos juostoje (paprastai 50–200 m nuo "
                "vandens telkinio). Toje juostoje nauja statyba iš esmės draudžiama — "
                "tikrink prieš mokėdamas dalyvio mokestį.")
    else:
        findings.append({"topic": "Vieta", "text": nature.get("note", "Vieta nenustatyta"),
                         "weight": "bad"})
        actions.append("Įrašyk gyvenvietę arba tikslias koordinates, kad būtų galima "
                       "įvertinti gamtą ir vandenį.")

    for pa in nature.get("protected_areas", [])[:4]:
        blockers.append(
            f"Patenka į saugomos teritorijos gaubtą: {pa['name']} ({pa['kind']}). "
            f"Tai apytikslis rėžis, ne riba — patvirtink STK ir REGIA žemėlapiuose.")

    # ---- hard flags already ticked
    for label in candidate.get("hard_flags_tripped", []):
        blockers.append(f"STOP vėliava pažymėta: {label}.")

    # ---- money
    price = candidate.get("price_eur")
    total = candidate.get("total_cost")
    ceiling = settings.get("budget_ceiling_eur")
    if price and total:
        uplift = total / price
        findings.append({
            "topic": "Kaštai",
            "text": f"Pirkimo kaina {price:,.0f} EUR, visi kaštai {total:,.0f} EUR "
                    f"(x{uplift:.1f}).".replace(",", " "),
            "weight": "bad" if ceiling and total > ceiling else "good"})
        if uplift >= 2.0:
            findings.append({"topic": "Kaštai",
                             "text": "Daugiau nei pusė projekto – ne pirkimo kaina. "
                                     "Derėtis dėl kainos beveik neverta; lemia sąmata.",
                             "weight": "neutral"})
    elif price and not total:
        actions.append("Užpildyk kaštų sąmatą — be jos kaina nieko nesako.")

    # ---- market context
    if market and candidate.get("municipality"):
        row = next((m for m in market if m["municipality"] == candidate["municipality"]), None)
        if row:
            pct = row["pct_power_water"] * 100
            findings.append({
                "topic": "Rinka",
                "text": f"{row['municipality']}: {pct:.1f}% vienbučių turi registruotą "
                        f"elektrą ir vandentiekį, {row['pct_pre_1945']*100:.0f}% statyti "
                        f"iki 1945 m.",
                "weight": "good" if pct >= 25 else ("bad" if pct < 12 else "neutral")})
            if pct < 12:
                actions.append("Šiame rajone infrastruktūra reta — ESO prijungimo kainą "
                               "tikrink pirmiausia, ji gali viršyti pirkimo kainą.")

    # ---- checklist progress
    done = sum(1 for v in (candidate.get("checks") or {}).values() if v)
    if done < 3:
        actions.append("Atlik patikros žingsnius 1–3 (NTR išrašas, saugomos teritorijos, "
                       "ESO). Jie kainuoja ~5 EUR ir atmeta daugumą objektų.")

    if candidate.get("verdict") == "incomplete":
        actions.append("Užpildyk visus dešimt balų — kol jų nėra, verdiktas neskaičiuojamas.")

    stance = ("Netinka" if blockers else
              "Verta apžiūros" if candidate.get("verdict") == "shortlist" else
              "Laikyti stebėjime")

    return {"stance": stance, "findings": findings,
            "blockers": blockers, "actions": actions}
