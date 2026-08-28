"""Dampening evidence reconstruction (src/cde/prioritization/dampening_evidence.py)."""
import pandas as pd

from cde.prioritization.dampening_evidence import build_dampening_evidence

DP = pd.Timestamp("2026-08-28")

CFG = {
    "dampening": {"mode": "multiply", "periods": 2, "multiplier": 0.5},
    "coaching_history_map": {"coaching_history_map": {
        "map_key": "behavior_selected",
        "count_status": ["Submitted", "Excused"],
        "count_types": ["ADAPT"],
        "behavior_to_topic": {"Talk Time": "Reduce Talk Time", "Resolution": "Improve Resolution Rate"},
    }},
}


def _candidates():
    # A1: dampened Reduce Talk Time; A2: dampened Improve Resolution Rate; A3: NOT dampened.
    return pd.DataFrame([
        {"agent_id": "A1", "period": DP, "call_type": "all", "topic": "Reduce Talk Time",
         "metric": "talk_time", "value": 300.0, "benchmark": 280.0, "gap": 20.0,
         "level_score": 0.4, "priority_score": 0.10, "dampened": True},
        {"agent_id": "A2", "period": DP, "call_type": "all", "topic": "Improve Resolution Rate",
         "metric": "resolution_rate", "value": 0.6, "benchmark": 0.8, "gap": -0.2,
         "level_score": 0.5, "priority_score": 0.15, "dampened": True},
        {"agent_id": "A3", "period": DP, "call_type": "all", "topic": "Reduce Talk Time",
         "metric": "talk_time", "value": 260.0, "benchmark": 280.0, "gap": -20.0,
         "level_score": 0.0, "priority_score": 0.30, "dampened": False},
    ])


def _coaching():
    return pd.DataFrame([
        # A1 coached on Talk Time twice within window (ADAPT)
        {"agent_id": "A1", "coaching_date": "2026-08-18", "period": "2026-08-21",
         "coaching_status": "Submitted", "coaching_type": "ADAPT", "behavior_selected": "Talk Time"},
        {"agent_id": "A1", "coaching_date": "2026-08-25", "period": "2026-08-28",
         "coaching_status": "Submitted", "coaching_type": "ADAPT", "behavior_selected": "Talk Time"},
        # A2 coached on Resolution within window (ADAPT)
        {"agent_id": "A2", "coaching_date": "2026-08-20", "period": "2026-08-21",
         "coaching_status": "Submitted", "coaching_type": "ADAPT", "behavior_selected": "Resolution"},
        # A2 also has an "In The Game" coaching -> excluded by count_types
        {"agent_id": "A2", "coaching_date": "2026-08-27", "period": "2026-08-28",
         "coaching_status": "Submitted", "coaching_type": "In The Game", "behavior_selected": "Resolution"},
    ])


def test_evidence_rows_and_fields():
    ev = build_dampening_evidence(_candidates(), _coaching(), CFG)
    assert set(ev["agent_id"]) == {"A1", "A2"}  # A3 not dampened -> excluded
    a1 = ev[ev["agent_id"] == "A1"].iloc[0]
    assert a1["topic"] == "Reduce Talk Time"
    assert a1["n_coaching_events"] == 2
    assert a1["coaching_behaviors"] == "Talk Time"
    assert a1["last_coached_period"] == "2026-08-28"
    # multiply mode: original = dampened / 0.5
    assert abs(a1["priority_score_dampened"] - 0.10) < 1e-9
    assert abs(a1["priority_score_original"] - 0.20) < 1e-9


def test_count_types_excludes_non_adapt_coaching():
    # A2's "In The Game" coaching (period 2026-08-28) is filtered out; only the ADAPT one counts.
    ev = build_dampening_evidence(_candidates(), _coaching(), CFG)
    a2 = ev[ev["agent_id"] == "A2"].iloc[0]
    assert a2["n_coaching_events"] == 1
    assert a2["last_coached_period"] == "2026-08-21"  # the ADAPT event, not the excluded 08-28 one


def test_no_dampened_candidates_returns_empty():
    cands = _candidates()
    cands["dampened"] = False
    ev = build_dampening_evidence(cands, _coaching(), CFG)
    assert ev.empty


def test_no_history_returns_empty():
    assert build_dampening_evidence(_candidates(), None, CFG).empty
