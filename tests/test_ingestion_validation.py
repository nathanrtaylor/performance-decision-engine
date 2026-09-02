"""Coaching-history normalization into the dampening grain (src/pde/ingestion/coaching_history.py)."""
import pandas as pd

from pde.ingestion.coaching_history import build_coaching_history

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


# ---- count_types allow-list -------------------------------------------------

_CFG_TYPES = {
    "coaching_history_map": {"coaching_history_map": {
        "map_key": "behavior_selected",
        "count_status": ["Submitted", "Excused"],
        "count_types": ["ADAPT"],
        "behavior_to_topic": {"Increased Transfer Rate": "Reduce Client Transfer Rate"},
    }}
}


def _raw_typed():
    return pd.DataFrame([
        {"agent_id": "1", "period": "2026-06-12", "coaching_status": "Submitted",
         "coaching_type": "ADAPT", "behavior_selected": "Increased Transfer Rate"},
        {"agent_id": "2", "period": "2026-06-12", "coaching_status": "Submitted",
         "coaching_type": "In The Game", "behavior_selected": "Increased Transfer Rate"},
    ])


def test_count_types_filters_to_allowlist():
    # Only the ADAPT row (agent 1) survives; "In The Game" (agent 2) is excluded.
    hist = build_coaching_history({"coaching_history": _raw_typed()}, _CFG_TYPES)
    assert hist is not None
    assert set(hist["agent_id"]) == {"1"}


def test_count_types_is_case_insensitive():
    raw = _raw_typed()
    raw.loc[0, "coaching_type"] = "adapt"  # lowercase variant still matches
    hist = build_coaching_history({"coaching_history": raw}, _CFG_TYPES)
    assert hist is not None and set(hist["agent_id"]) == {"1"}


def test_count_types_empty_counts_all_types():
    # No count_types configured => all types count (backward compatible).
    hist = build_coaching_history({"coaching_history": _raw_typed()}, CFG)
    assert hist is not None and set(hist["agent_id"]) == {"1", "2"}


def test_count_types_skipped_when_column_absent():
    # count_types set but no coaching_type column => filter skipped (cannot enforce).
    raw = pd.DataFrame([
        {"agent_id": "1", "period": "2026-06-12", "coaching_status": "Submitted",
         "behavior_selected": "Increased Transfer Rate"},
    ])
    hist = build_coaching_history({"coaching_history": raw}, _CFG_TYPES)
    assert hist is not None and set(hist["agent_id"]) == {"1"}
