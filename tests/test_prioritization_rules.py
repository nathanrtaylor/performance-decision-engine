"""Versioned weight resolution + governance enforcement (src/cde/prioritization)."""
import pandas as pd
import pytest

from cde.prioritization.weights import get_metric_weight
from cde.prioritization.apply import build_topic_candidates

CFG = {
    "priority_model": {"w_level": 0.5, "w_trend": 0.2, "w_risk": 0.3, "w_confidence": 0.0},
    "priorities": {"by_category": {"business": 1.0, "quality_behavior": 0.3}},
    "metric_catalog": {
        "metric_catalog": {
            "metrics": {
                "transfer_rate": {"category": "business", "direction": "lower_is_better",
                                  "eligible_for_prioritization": True},
                "transition_statement": {"category": "quality_behavior", "direction": "higher_is_better",
                                         "eligible_for_prioritization": False},
            },
            "governance": {"disallow_prioritization_if_not_flagged": True},
        }
    },
    "topic_map": {
        "topic_map": {
            "metric_to_topic": {
                "transfer_rate": "Reduce Client Transfer Rate",
                "transition_statement": "Use Clear Transition Statements",
            }
        }
    },
    "eligibility": {"min_confidence": 0.0},
}


def test_metric_weight_resolves_via_category():
    # This is the fix: previously both returned the 0.0 default and zeroed priority_score.
    assert get_metric_weight("transfer_rate", "all", CFG) == 1.0
    assert get_metric_weight("transition_statement", "all", CFG) == 0.3


def _scores():
    return pd.DataFrame([
        {"agent_id": "A", "period": pd.Timestamp("2026-06-19"), "call_type": "all",
         "metric": "transfer_rate", "score_level": 0.5, "score_trend": 0.0, "score_risk": 0.0,
         "score_confidence": 0.9, "score_total": 0.25},
        {"agent_id": "A", "period": pd.Timestamp("2026-06-19"), "call_type": "all",
         "metric": "transition_statement", "score_level": 0.9, "score_trend": 0.0, "score_risk": 0.0,
         "score_confidence": 0.9, "score_total": 0.9},
    ])


def test_priority_score_is_nonzero_after_weight_fix():
    scores = _scores()
    signals = scores[["agent_id", "period", "call_type", "metric"]].copy()
    cands = build_topic_candidates(signals, scores, CFG)
    tr = cands[cands["topic"] == "Reduce Client Transfer Rate"]
    assert not tr.empty
    assert float(tr["priority_score"].iloc[0]) > 0  # 0.25 * 1.0 * 1.0


def test_evidence_from_window_aggregates_when_pointintime_missing():
    # Agent has window aggregates (level_8w/benchmark_8w) but NO point-in-time row at window_end.
    # Evidence must still populate from the window averages (not blank).
    scores = _scores().copy()
    scores["level_8w"] = [0.06, 0.90]        # mean gap over window
    scores["benchmark_8w"] = [0.12, 0.50]
    scores["direction"] = ["lower_is_better", "higher_is_better"]
    signals = pd.DataFrame(columns=["agent_id", "period", "call_type", "metric"])  # nothing to join
    cands = build_topic_candidates(signals, scores, CFG)
    tr = cands[cands["topic"] == "Reduce Client Transfer Rate"].iloc[0]
    assert tr["benchmark"] == 0.12
    assert tr["gap"] == pytest.approx(0.06)
    assert tr["value"] == pytest.approx(0.18)   # benchmark + gap


def test_ineligible_metric_is_dropped_from_prioritization():
    scores = _scores()
    signals = scores[["agent_id", "period", "call_type", "metric"]].copy()
    cands = build_topic_candidates(signals, scores, CFG)
    # transition_statement has eligible_for_prioritization: false -> its topic must not appear
    assert "Use Clear Transition Statements" not in set(cands["topic"])
    assert "Reduce Client Transfer Rate" in set(cands["topic"])
