"""Windowed aggregation: sample-size-aware confidence + thin-window drop (temporal/aggregate.py)."""
import pandas as pd

from cde.temporal.aggregate import aggregate_scores_window

CFG = {
    "metric_catalog": {"metric_catalog": {"metrics": {
        "resolution_rate": {"computation_override": {"denominator_min": 30}, "direction": "higher_is_better"},
    }}},
    "temporal": {
        "window_weeks": 4, "min_weeks_for_trend": 3,
        "volume_target_weeks": 4,   # full credit at 30*4 = 120 total
        "min_window_weeks": 2,      # drop below 30*2 = 60 total
        "include_recency_shift": False,
    },
}
PERIODS = pd.to_datetime(["2026-06-05", "2026-06-12", "2026-06-19", "2026-06-26"])


def _agent(aid, denom):
    return pd.DataFrame([
        {"agent_id": aid, "period": p, "call_type": "all", "metric": "resolution_rate",
         "value": 0.80, "gap": 0.02, "benchmark": 0.78, "direction": "higher_is_better",
         "denominator": denom}
        for p in PERIODS
    ])


def test_confidence_reflects_sample_size_not_just_coverage():
    # Same week-coverage (4/4) but different total sample -> different confidence.
    df = pd.concat([_agent("HIGH", 40), _agent("MED", 20)], ignore_index=True)
    out = aggregate_scores_window(df, CFG).set_index("agent_id")
    assert out.loc["HIGH", "confidence_8w"] == 1.0                      # 160 >= 120 target
    assert out.loc["MED", "confidence_8w"] < out.loc["HIGH", "confidence_8w"]
    assert abs(out.loc["MED", "confidence_8w"] - (80 / 120)) < 1e-9      # coverage 1.0 x volume 0.667


def test_thin_window_is_dropped():
    df = pd.concat([_agent("HIGH", 40), _agent("LOW", 5)], ignore_index=True)
    out = aggregate_scores_window(df, CFG)
    ids = set(out["agent_id"])
    assert "HIGH" in ids
    assert "LOW" not in ids   # total denom 20 < floor 60


def test_denom_8w_is_reported():
    out = aggregate_scores_window(_agent("HIGH", 40), CFG)
    assert out.iloc[0]["denom_8w"] == 160


def test_min_window_weeks_zero_disables_drop():
    cfg = {**CFG, "temporal": {**CFG["temporal"], "min_window_weeks": 0}}
    out = aggregate_scores_window(pd.concat([_agent("HIGH", 40), _agent("LOW", 5)], ignore_index=True), cfg)
    assert {"HIGH", "LOW"} <= set(out["agent_id"])   # nothing dropped
