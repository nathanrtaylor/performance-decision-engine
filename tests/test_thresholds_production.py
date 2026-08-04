"""Production-mode evidence gating (src/cde/signals/thresholds.py)."""
import numpy as np
import pandas as pd

from cde.signals.thresholds import apply_signal_thresholds

P = pd.Timestamp("2026-07-31")

CFG = {
    "thresholds": {"signal_thresholds": {
        "mode": "production",
        "global": {"min_confidence": 0.30, "max_volatility": None, "min_denominator_default": 10},
        "by_category": {"business": {"min_confidence": 0.30, "min_denominator_default": 10}},
        "candidate_rules": {
            "require_reference_point": True,
            "require_bad_magnitude": False,   # materiality deferred to abstention floor
            "require_bad_trend": False,
            "only_worsening": False,
        },
    }},
    "metric_catalog": {"metric_catalog": {"metrics": {
        "m1": {"category": "business"},
        "m2": {"category": "business"},
    }}},
}


def _sig(rows):
    cols = ["agent_id", "metric", "value", "gap", "confidence", "denominator", "direction"]
    df = pd.DataFrame(rows, columns=cols)
    df["period"] = P
    df["call_type"] = "all"
    df["trend"] = np.nan
    df["volatility"] = np.nan
    return df


def test_production_keeps_small_real_deficit_and_gates_evidence():
    sig = _sig([
        # kept: small but real deficit, good confidence + denominator, has reference (gap)
        ("keep", "m1", 0.80, -0.05, 0.80, 50, "higher_is_better"),
        # excluded: confidence below floor
        ("lowconf", "m1", 0.70, -0.10, 0.10, 50, "higher_is_better"),
        # excluded: denominator below floor
        ("lowden", "m1", 0.70, -0.10, 0.80, 3, "higher_is_better"),
        # excluded: no reference point (gap NaN, single-row metric -> z NaN)
        ("noref", "m2", 0.70, np.nan, 0.80, 50, "higher_is_better"),
    ])
    res = apply_signal_thresholds(sig, CFG)
    eligible_ids = set(res.eligible_signals["agent_id"])
    assert "keep" in eligible_ids                       # magnitude gate OFF -> small deficit survives
    assert {"lowconf", "lowden", "noref"}.isdisjoint(eligible_ids)

    reasons = {r["agent_id"]: set(r["exclusion_reasons"]) for _, r in res.excluded_signals.iterrows()}
    assert "LOW_CONFIDENCE" in reasons["lowconf"]
    assert "LOW_DENOMINATOR" in reasons["lowden"]
    assert "NO_REFERENCE_POINT" in reasons["noref"]
