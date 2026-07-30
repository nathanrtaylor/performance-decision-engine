"""Percentile-based, direction-aware scoring (src/cde/scoring/assemble.py)."""
import pandas as pd

from cde.scoring.assemble import compute_windowed_scores

CFG = {"priority_model": {"w_level": 0.5, "w_trend": 0.2, "w_risk": 0.3, "w_confidence": 0.0}}


def _wrow(**kw):
    base = dict(
        call_type="all",
        window_start=pd.Timestamp("2026-05-01"),
        window_end=pd.Timestamp("2026-06-19"),
        weeks_present=8,
        volatility_8w=0.0,
        trend_8w=0.0,
        benchmark_8w=0.10,
    )
    base.update(kw)
    return base


def _population(metric, direction, gaps, benchmark=0.10):
    """One row per agent for a single metric; level_8w is the agent's mean gap vs benchmark."""
    return pd.DataFrame([
        _wrow(agent_id=f"A{i}", metric=metric, direction=direction,
              level_8w=g, benchmark_8w=benchmark, confidence_8w=0.9)
        for i, g in enumerate(gaps)
    ])


def test_overperformers_score_zero_lower_is_better():
    # lower_is_better: negative gap = beating benchmark = good = 0
    w = _population("transfer_rate", "lower_is_better", gaps=[-0.05, -0.02, 0.0, 0.03, 0.08])
    s = compute_windowed_scores(w, CFG).set_index("agent_id")
    assert s.loc["A0", "score_level"] == 0.0            # best performer
    assert s.loc["A4", "score_level"] > 0.0             # worst performer
    assert s["score_level"].max() <= 1.0                # bounded


def test_worst_performer_ranks_highest():
    w = _population("crt", "lower_is_better", gaps=[10.0, 200.0, 800.0, 1500.0])  # large scalars
    s = compute_windowed_scores(w, CFG).sort_values("level_8w")
    # score_level must be monotonically non-decreasing with the (bad) gap
    assert list(s["score_level"]) == sorted(s["score_level"])


def test_scores_are_scale_free_across_metrics():
    # A metric with huge scalars (crt) must not out-score a small-scalar metric (transfer_rate)
    # purely due to magnitude. Worst agent of each should reach a comparable ceiling.
    crt = _population("crt", "lower_is_better", gaps=[-500, 0, 500, 1500], benchmark=800)
    tr = _population("transfer_rate", "lower_is_better", gaps=[-0.05, 0.0, 0.03, 0.09], benchmark=0.12)
    both = pd.concat([crt, tr], ignore_index=True)
    s = compute_windowed_scores(both, CFG)
    crt_max = s[s["metric"] == "crt"]["score_level"].max()
    tr_max = s[s["metric"] == "transfer_rate"]["score_level"].max()
    assert abs(crt_max - tr_max) < 1e-9   # same percentile ceiling despite 1000x scalar difference


def test_higher_is_better_direction():
    # higher_is_better: low value = bad. Agent below benchmark scores; above scores 0.
    w = _population("resolution_rate", "higher_is_better",
                    gaps=[-0.10, -0.02, 0.05, 0.15], benchmark=0.78)
    s = compute_windowed_scores(w, CFG).sort_values("level_8w")
    assert s.iloc[0]["score_level"] > 0.0   # most below benchmark
    assert s.iloc[-1]["score_level"] == 0.0  # most above benchmark


def test_risk_is_level_times_low_confidence():
    w = _population("transfer_rate", "lower_is_better", gaps=[-0.05, 0.0, 0.05, 0.09])
    w["confidence_8w"] = 0.25
    s = compute_windowed_scores(w, CFG)
    assert ((s["score_risk"] - s["score_level"] * (1 - 0.25)).abs() < 1e-9).all()


def test_empty_input_returns_well_formed_frame():
    s = compute_windowed_scores(pd.DataFrame(), CFG)
    assert s.empty
    for c in ["agent_id", "period", "call_type", "metric", "score_total"]:
        assert c in s.columns
