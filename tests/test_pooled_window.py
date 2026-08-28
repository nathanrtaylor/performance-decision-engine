"""Pooled (denominator-weighted) windowed level for opted-in metrics (temporal/aggregate.py).

A metric with computation_override.window_aggregation == "pooled" scores its windowed level as
Sum(numerator)/Sum(denominator) - benchmark over the window, instead of the mean of weekly gaps.
This nets a week's numerator (e.g. cancellations) against sales that occurred in OTHER weeks,
which the mean-of-weekly-rate level understates when the same-week denominator is null.
"""
import numpy as np
import pandas as pd

from cde.temporal.aggregate import aggregate_scores_window

PERIODS = pd.to_datetime(["2026-06-05", "2026-06-12", "2026-06-19", "2026-06-26"])
BENCH = 0.10


def _cancel_rows(aid, pooled_flag):
    # 2 sales weeks (denominator present, 1 cancel each) + 2 cancel-only weeks (denominator null).
    # Sum(num) = 1+1+3+2 = 7 ; Sum(den) = 10+10 = 20 ; pooled rate = 0.35 -> pooled level = 0.25.
    # Mean of weekly gaps = mean(0, 0, -0.10, -0.10) = -0.05  (the understated status quo).
    specs = [(10, 1), (10, 1), (np.nan, 3), (np.nan, 2)]
    rows = []
    for p, (den, num) in zip(PERIODS, specs):
        val = 0.0 if pd.isna(den) or den == 0 else num / den
        rows.append({
            "agent_id": aid, "period": p, "call_type": "all", "metric": "cancel_rate",
            "value": val, "gap": val - BENCH, "benchmark": BENCH, "direction": "lower_is_better",
            "numerator": num, "denominator": den,
        })
    return pd.DataFrame(rows)


def _cfg(pooled: bool):
    ov = {"denominator_min": 1}
    if pooled:
        ov["window_aggregation"] = "pooled"
    return {
        "metric_catalog": {"metric_catalog": {"metrics": {
            "cancel_rate": {"computation_override": ov, "direction": "lower_is_better"},
        }}},
        "temporal": {"window_weeks": 4, "min_weeks_for_trend": 3, "min_window_weeks": 0,
                     "include_recency_shift": False},
    }


def test_pooled_level_is_pooled_rate_minus_benchmark():
    out = aggregate_scores_window(_cancel_rows("A", pooled_flag=True), _cfg(pooled=True))
    row = out.iloc[0]
    assert abs(row["level_8w"] - 0.25) < 1e-9          # (7/20) - 0.10
    # value reconstructs to the pooled rate (level + benchmark)
    assert abs(row["level_8w"] + row["benchmark_8w"] - 0.35) < 1e-9
    assert row["denom_8w"] == 20                        # numerator column not emitted


def test_without_flag_falls_back_to_mean_of_weekly_gaps():
    # Same data, no pooled flag -> the understated mean-of-weekly-gap level.
    out = aggregate_scores_window(_cancel_rows("A", pooled_flag=False), _cfg(pooled=False))
    assert abs(out.iloc[0]["level_8w"] - (-0.05)) < 1e-9


def test_pooled_no_in_window_denominator_is_neutral():
    # Cancels but zero in-window sales (denominator all null) -> pooled rate undefined -> level 0.0.
    df = _cancel_rows("A", pooled_flag=True)
    df["denominator"] = np.nan
    out = aggregate_scores_window(df, _cfg(pooled=True))
    assert out.iloc[0]["level_8w"] == 0.0


def test_nonflagged_metric_unchanged():
    # A higher_is_better metric with no pooled flag keeps mean-of-weekly-gap level.
    rows = pd.DataFrame([
        {"agent_id": "R", "period": p, "call_type": "all", "metric": "resolution_rate",
         "value": 0.80, "gap": 0.02, "benchmark": 0.78, "direction": "higher_is_better",
         "numerator": 8, "denominator": 10}
        for p in PERIODS
    ])
    cfg = {
        "metric_catalog": {"metric_catalog": {"metrics": {
            "resolution_rate": {"computation_override": {"denominator_min": 1}, "direction": "higher_is_better"},
        }}},
        "temporal": {"window_weeks": 4, "min_window_weeks": 0, "include_recency_shift": False},
    }
    out = aggregate_scores_window(rows, cfg)
    assert abs(out.iloc[0]["level_8w"] - 0.02) < 1e-9
