"""Pooled per-agent aggregation in benchmarks_recalc (prep.windowed_mean_per_agent).

Mirrors the engine's pooled windowing so recalc proposes cancel_rate on the same scale the engine
scores: per-agent Sum(numerator)/Sum(denominator), keeping null-denominator numerator weeks.
"""
import numpy as np
import pandas as pd

from pde.benchmarks_recalc.prep import windowed_mean_per_agent


def _df():
    return pd.DataFrame([
        # Agent A: a sales week (den 10, 1 cancel) + a cancel-only week (den null, 3 cancels).
        {"agent_id": "A", "metric_key": "cancel_rate", "calc": 0.1, "numerator": 1, "denominator": 10},
        {"agent_id": "A", "metric_key": "cancel_rate", "calc": 0.0, "numerator": 3, "denominator": np.nan},
        # Agent B: a thin week (den 2 < min, dropped) + a valid week (den 5, 1 cancel).
        {"agent_id": "B", "metric_key": "cancel_rate", "calc": 0.5, "numerator": 1, "denominator": 2},
        {"agent_id": "B", "metric_key": "cancel_rate", "calc": 0.2, "numerator": 1, "denominator": 5},
    ])


def test_pooled_rate_per_agent_keeps_null_denominator_numerator():
    wm = windowed_mean_per_agent(_df(), "cancel_rate", cohort_col=None, denominator_min=3, pooled=True)
    by = wm.set_index("agent_id")["mean_calc"]
    assert abs(by["A"] - 0.4) < 1e-9    # (1+3) / 10  (null-den cancel week's numerator counts)
    assert abs(by["B"] - 0.2) < 1e-9    # (1) / 5     (thin den=2 week dropped)


def test_mean_path_unchanged_when_not_pooled():
    wm = windowed_mean_per_agent(_df(), "cancel_rate", cohort_col=None, denominator_min=3, pooled=False)
    by = wm.set_index("agent_id")["mean_calc"]
    # mean of weekly calc over den>=3 weeks: A -> week1 only (0.1); B -> week2 only (0.2)
    assert abs(by["A"] - 0.1) < 1e-9
    assert abs(by["B"] - 0.2) < 1e-9


def test_pooled_all_null_denominator_drops_agent():
    df = pd.DataFrame([
        {"agent_id": "C", "metric_key": "cancel_rate", "calc": 0.0, "numerator": 2, "denominator": np.nan},
    ])
    wm = windowed_mean_per_agent(df, "cancel_rate", cohort_col=None, denominator_min=3, pooled=True)
    assert wm.empty  # no in-window denominator -> undefined -> dropped
