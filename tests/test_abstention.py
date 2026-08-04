"""Abstention floor + visible non-recommendations (src/cde/engine/abstain.py)."""
import pandas as pd

from cde.engine.abstain import (
    REASON_BELOW_FLOOR, REASON_NO_SIGNAL, apply_abstention,
)

P = pd.Timestamp("2026-07-31")
CFG = {"abstention": {"enabled": True, "min_priority_score": 0.10}}


def _recs(rows):
    """rows: (agent, tier, priority_score, topic)."""
    out = []
    for agent, tier, ps, topic in rows:
        out.append({"agent_id": agent, "period": P, "call_type": "all",
                    "topic": topic, "priority_score": ps, "tier": tier, "level_score": 0.2})
    return pd.DataFrame(out)


def _agents(ids):
    rows = [{"agent_id": a, "week_ending": P} for a in ids]
    return pd.DataFrame(rows)


def test_below_floor_single_abstains_others_kept():
    recs = _recs([
        ("a1", "single", 0.50, "Reduce Hold Time"),   # kept
        ("a2", "single", 0.02, "Reduce Talk Time"),   # below floor -> abstained
        ("a3", "break_glass", 0.02, "Reduce Cancel Rate"),  # never abstained
        ("a4", "theme", 0.02, "Call Control"),         # never abstained
    ])
    kept, abst = apply_abstention(recs, _agents(["a1", "a2", "a3", "a4"]), None, CFG)

    assert set(kept["agent_id"]) == {"a1", "a3", "a4"}
    a2 = abst[abst["agent_id"] == "a2"].iloc[0]
    assert a2["reason"] == REASON_BELOW_FLOOR
    assert a2["best_topic"] == "Reduce Talk Time"
    assert a2["best_priority_score"] == 0.02
    # break_glass / theme below floor are NOT abstained
    assert set(abst["agent_id"]) == {"a2"}


def test_universe_agent_with_no_rec_is_no_signal():
    recs = _recs([("a1", "single", 0.50, "Reduce Hold Time")])
    kept, abst = apply_abstention(recs, _agents(["a1", "a2"]), None, CFG)
    assert set(kept["agent_id"]) == {"a1"}
    a2 = abst[abst["agent_id"] == "a2"].iloc[0]
    assert a2["reason"] == REASON_NO_SIGNAL
    assert pd.isna(a2["best_topic"])


def test_partition_is_complete_and_disjoint():
    recs = _recs([
        ("a1", "single", 0.50, "T1"),
        ("a2", "single", 0.01, "T2"),   # below floor
    ])
    universe = ["a1", "a2", "a3"]        # a3 = no rec
    kept, abst = apply_abstention(recs, _agents(universe), None, CFG)
    kept_ids, abst_ids = set(kept["agent_id"]), set(abst["agent_id"])
    assert kept_ids | abst_ids == set(universe)
    assert kept_ids & abst_ids == set()


def test_no_signal_pulls_best_available_from_candidates():
    recs = _recs([("a1", "single", 0.50, "T1")])
    # a2 has a candidate that never became a rec (e.g. filtered by eligibility)
    candidates = pd.DataFrame([
        {"agent_id": "a2", "period": P, "call_type": "all", "topic": "Reduce CRT",
         "priority_score": 0.30, "level_score": 0.4},
    ])
    _, abst = apply_abstention(recs, _agents(["a1", "a2"]), candidates, CFG)
    a2 = abst[abst["agent_id"] == "a2"].iloc[0]
    assert a2["reason"] == REASON_NO_SIGNAL
    assert a2["best_topic"] == "Reduce CRT"
    assert a2["best_priority_score"] == 0.30


def test_disabled_is_noop():
    recs = _recs([("a1", "single", 0.01, "T1")])
    kept, abst = apply_abstention(recs, _agents(["a1", "a2"]), None, {"abstention": {"enabled": False}})
    assert len(kept) == 1 and abst.empty
