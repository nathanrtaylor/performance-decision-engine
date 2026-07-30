"""Recency dampening window math + no-op behavior (src/cde/prioritization/dampening.py)."""
import pandas as pd

from cde.prioritization.dampening import apply_recent_coaching_dampening

CFG = {"dampening": {"mode": "multiply", "periods": 2, "multiplier": 0.5}}
DECISION = pd.Timestamp("2026-06-19")


def _cands():
    return pd.DataFrame([
        {"agent_id": "12345", "topic": "Reduce Client Transfer Rate", "period": DECISION, "priority_score": 1.0},
        {"agent_id": "12345", "topic": "Improve Resolution Rate", "period": DECISION, "priority_score": 1.0},
        {"agent_id": "12345", "topic": "Reduce Cancel Rate", "period": DECISION, "priority_score": 1.0},
    ])


def _history():
    return pd.DataFrame([
        {"agent_id": "12345", "topic": "Reduce Client Transfer Rate",
         "last_coached_period": pd.Timestamp("2026-06-12")},   # 1 week ago -> within window
        {"agent_id": "12345", "topic": "Reduce Cancel Rate",
         "last_coached_period": pd.Timestamp("2026-05-08")},   # ~6 weeks ago -> outside window
    ])


def test_multiply_dampens_only_within_window():
    out = apply_recent_coaching_dampening(_cands(), CFG, history=_history()).set_index("topic")
    assert out.loc["Reduce Client Transfer Rate", "priority_score"] == 0.5
    assert bool(out.loc["Reduce Client Transfer Rate", "dampened"]) is True
    # not in history at all -> unchanged
    assert out.loc["Improve Resolution Rate", "priority_score"] == 1.0
    assert bool(out.loc["Improve Resolution Rate", "dampened"]) is False
    # coached too long ago -> unchanged
    assert out.loc["Reduce Cancel Rate", "priority_score"] == 1.0
    assert bool(out.loc["Reduce Cancel Rate", "dampened"]) is False


def test_no_history_is_noop():
    out = apply_recent_coaching_dampening(_cands(), CFG, history=None)
    assert (out["priority_score"] == 1.0).all()
    assert (~out["dampened"]).all()


def test_suppress_mode_removes_recent():
    cfg = {"dampening": {"mode": "suppress", "periods": 2, "multiplier": 0.5}}
    out = apply_recent_coaching_dampening(_cands(), cfg, history=_history())
    assert "Reduce Client Transfer Rate" not in set(out["topic"])
    assert "Improve Resolution Rate" in set(out["topic"])


def test_agent_id_format_drift_still_matches():
    # candidate id has leading zeros / float form; history has plain int -> must still match
    cands = _cands()
    cands.loc[cands["topic"] == "Reduce Client Transfer Rate", "agent_id"] = "012345.0"
    out = apply_recent_coaching_dampening(cands, CFG, history=_history()).set_index("topic")
    assert out.loc["Reduce Client Transfer Rate", "priority_score"] == 0.5
