"""Scoring engine. Mirrors the spreadsheet exactly so the two never disagree.

Two stages:
  1. Hard fails  — any true flag rejects the candidate outright, no score computed.
  2. Weighted score — 10 criteria, each 0..10, weights summing to 1.0.

All weights and cost assumptions live in the settings table so the dashboard can
change them at runtime without a redeploy.

Reference case (must keep passing): the Lazdijai listing scores 6.56 with a total
cost of 42,613 EUR. See AGENT.md section 2.
"""
from __future__ import annotations
from typing import Any

CRITERIA = [
    ("forest_water",  "Miškas / vanduo",           0.15),
    ("isolation",     "Izoliacija",                0.12),
    ("power",         "Elektra",                   0.15),
    ("water",         "Vanduo",                    0.10),
    ("condition",     "Pastato būklė",             0.12),
    ("plot_size",     "Sklypo dydis",              0.08),
    ("access",        "Privažiavimas",             0.08),
    ("buildability",  "Statybos galimybė",         0.10),
    ("price_vs_value","Kaina vs vertinimas",       0.06),
    ("geopolitics",   "Geopolitika",               0.04),
]

HARD_FLAGS = [
    ("fractional_ownership", "Dalinė nuosavybė"),
    ("building_without_land", "Pastatas be žemės"),
    ("construction_banned",  "Statyba draudžiama"),
    ("no_legal_access",      "Nėra teisinio privažiavimo"),
    ("heritage_listed",      "Kultūros paveldas"),
    ("occupants_registered", "Deklaruoti gyventojai"),
]

COST_LINES = [
    ("purchase",      "Pirkimo kaina"),
    ("deposit_lost",  "Prarastas dalyvio mokestis"),
    ("registration",  "Registracija + notaras"),
    ("registry_extract", "NTR išrašas"),
    ("travel",        "Kelionė / apžiūra"),
    ("power_connect", "ESO prijungimas"),
    ("borehole",      "Gręžinys + siurblys"),
    ("septic",        "Nuotekos"),
    ("roof",          "Stogas"),
    ("structure",     "Sienos / pamatai"),
    ("interior",      "Vidaus remontas"),
]

DEFAULT_SETTINGS: dict[str, Any] = {
    "weights": {k: w for k, _, w in CRITERIA},
    "budget_ceiling_eur": 25000.0,
    "min_score": 6.0,
    "contingency_pct": 0.15,
    "auto_costs": {
        "registration": 400.0,
        "registry_extract": 5.0,
        "travel": 250.0,
    },
}


def normalised_weights(raw: dict[str, float] | None) -> dict[str, float]:
    """Return weights summing to exactly 1.0. Falls back to defaults on garbage."""
    base = {k: w for k, _, w in CRITERIA}
    if raw:
        for k in base:
            v = raw.get(k)
            if isinstance(v, (int, float)) and v >= 0:
                base[k] = float(v)
    total = sum(base.values())
    if total <= 0:
        return {k: w for k, _, w in CRITERIA}
    return {k: v / total for k, v in base.items()}


def evaluate(candidate: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """Score one candidate. Pure function — no I/O, trivially testable."""
    flags = candidate.get("flags") or {}
    scores = candidate.get("scores") or {}
    costs = candidate.get("costs") or {}

    tripped = [label for key, label in HARD_FLAGS if bool(flags.get(key))]

    weights = normalised_weights(settings.get("weights"))
    scored = {k: scores.get(k) for k, _, _ in CRITERIA}
    complete = all(isinstance(v, (int, float)) for v in scored.values())
    weighted = (
        round(sum(float(scored[k]) * weights[k] for k, _, _ in CRITERIA), 3)
        if complete else None
    )

    subtotal = sum(float(costs.get(k) or 0) for k, _ in COST_LINES)
    contingency = round(subtotal * float(settings.get("contingency_pct", 0.15)), 0)
    total_cost = round(subtotal + contingency, 0) if subtotal > 0 else None

    ceiling = float(settings.get("budget_ceiling_eur", 25000))
    min_score = float(settings.get("min_score", 6.0))

    if tripped:
        verdict, reason = "rejected", "STOP: " + ", ".join(tripped)
    elif weighted is None:
        verdict, reason = "incomplete", "Trūksta balų"
    elif total_cost is not None and total_cost > ceiling:
        verdict, reason = "over_budget", f"Virš biudžeto ({total_cost:,.0f} > {ceiling:,.0f} EUR)"
    elif weighted >= min_score:
        verdict, reason = "shortlist", "Trumpasis sąrašas"
    else:
        verdict, reason = "weak", f"Balas {weighted} < {min_score}"

    return {
        "hard_flags_tripped": tripped,
        "weighted_score": weighted,
        "cost_subtotal": round(subtotal, 0) if subtotal else None,
        "cost_contingency": contingency if subtotal else None,
        "total_cost": total_cost,
        "eur_per_point": (
            round(total_cost / weighted, 0)
            if (total_cost and weighted and weighted > 0) else None
        ),
        "verdict": verdict,
        "verdict_reason": reason,
        "weights_used": weights,
    }
