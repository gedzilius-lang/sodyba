"""Locks the v1 scoring contract. These values must not move."""
from backend.app.scoring import DEFAULT_SETTINGS, evaluate, normalised_weights

ALL_CRITERIA = ["forest_water", "isolation", "power", "water", "condition",
                "plot_size", "access", "buildability", "price_vs_value", "geopolitics"]


def _candidate(score=7, **kw):
    base = {
        "flags": {},
        "scores": {k: score for k in ALL_CRITERIA},
        "costs": {"purchase": 10000, "roof": 5000},
    }
    base.update(kw)
    return base


def test_default_weights_sum_to_one():
    assert round(sum(DEFAULT_SETTINGS["weights"].values()), 6) == 1.0


def test_uniform_scores_produce_that_score():
    r = evaluate(_candidate(7), DEFAULT_SETTINGS)
    assert r["weighted_score"] == 7.0


def test_cost_contingency_and_eur_per_point():
    r = evaluate(_candidate(7), DEFAULT_SETTINGS)
    assert r["cost_subtotal"] == 15000
    assert r["cost_contingency"] == 2250
    assert r["total_cost"] == 17250
    assert r["eur_per_point"] == 2464          # 17250 / 7.0, rounded
    assert r["verdict"] == "shortlist"


def test_hard_flag_rejects_outright():
    r = evaluate(_candidate(9, flags={"fractional_ownership": True}),
                 DEFAULT_SETTINGS)
    assert r["verdict"] == "rejected"
    assert r["hard_flags_tripped"] == ["Dalinė nuosavybė"]
    assert r["verdict_reason"].startswith("STOP:")


def test_missing_scores_are_incomplete():
    c = _candidate(7)
    del c["scores"]["power"]
    r = evaluate(c, DEFAULT_SETTINGS)
    assert r["weighted_score"] is None
    assert r["verdict"] == "incomplete"


def test_weights_are_renormalised_when_they_do_not_sum_to_one():
    # power raised from 0.15 to 0.28 -> raw total 1.13
    w = normalised_weights({**DEFAULT_SETTINGS["weights"], "power": 0.28})
    assert round(sum(w.values()), 6) == 1.0
    assert round(w["power"], 5) == round(0.28 / 1.13, 5)


def test_garbage_weights_fall_back_to_defaults():
    w = normalised_weights({"power": -1, "water": "nonsense"})
    assert round(sum(w.values()), 6) == 1.0
    assert round(w["power"], 6) == round(DEFAULT_SETTINGS["weights"]["power"], 6)
