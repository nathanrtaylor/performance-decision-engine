"""Coaching-history normalization into the dampening grain (src/cde/ingestion/coaching_history.py)."""
import pandas as pd

from cde.ingestion.coaching_history import build_coaching_history

CFG = {
    "coaching_history_map": {
        "coaching_history_map": {
            "map_key": "behavior_selected",
            "count_status": ["Submitted", "Excused"],
            "behavior_to_topic": {"Increased Transfer Rate": "Reduce Client Transfer Rate"},
        }
    }
}


def _raw():
    return pd.DataFrame([
        # same agent+topic twice; keep the latest period
        {"agent_id": "012345", "coaching_date": "2026-06-10", "period": "2026-06-12",
         "coaching_status": "Submitted", "behavior_selected": "Increased Transfer Rate"},
        {"agent_id": "012345", "coaching_date": "2026-06-03", "period": "2026-06-05",
         "coaching_status": "Excused", "behavior_selected": "Increased Transfer Rate"},
        # unmapped behavior -> dropped
        {"agent_id": "999", "coaching_date": "2026-06-10", "period": "2026-06-12",
         "coaching_status": "Submitted", "behavior_selected": "Some Unmapped Behavior"},
        # uncounted status -> dropped
        {"agent_id": "888", "coaching_date": "2026-06-10", "period": "2026-06-12",
         "coaching_status": "No Show", "behavior_selected": "Increased Transfer Rate"},
    ])


def test_collapses_to_agent_topic_last_period():
    hist = build_coaching_history({"coaching_history": _raw()}, CFG)
    assert len(hist) == 1
    row = hist.iloc[0]
    assert row["agent_id"] == "12345"  # leading zeros normalized
    assert row["topic"] == "Reduce Client Transfer Rate"
    assert pd.Timestamp(row["last_coached_period"]) == pd.Timestamp("2026-06-12")  # latest kept


def test_missing_table_returns_none():
    assert build_coaching_history({}, CFG) is None


def test_mapping_is_case_insensitive():
    raw = pd.DataFrame([
        {"agent_id": "1", "period": "2026-06-12", "coaching_status": "Submitted",
         "behavior_selected": "increased TRANSFER rate"},  # casing differs from crosswalk key
    ])
    hist = build_coaching_history({"coaching_history": raw}, CFG)
    assert hist is not None and len(hist) == 1
    assert hist.iloc[0]["topic"] == "Reduce Client Transfer Rate"


def test_all_unmapped_returns_none():
    raw = pd.DataFrame([
        {"agent_id": "1", "period": "2026-06-12", "coaching_status": "Submitted",
         "behavior_selected": "Totally Unknown"},
    ])
    assert build_coaching_history({"coaching_history": raw}, CFG) is None
