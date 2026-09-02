"""Tests for the class-roster loader (the dashboard's source of truth)."""
from __future__ import annotations

import pandas as pd

from pde.training.roster import load_class_roster


def _write_roster(path):
    with pd.ExcelWriter(path) as w:
        pd.DataFrame({"Note": ["summary sheet is ignored"]}).to_excel(w, sheet_name="Summary", index=False)
        pd.DataFrame({
            "Expert Name": ["Ann A", "Bo B"],
            "EEID": [725820, 598990],
            "Trainer Name": ["T One & T Two", "T Three"],
            "Start Date": pd.to_datetime(["2026-08-24", "2026-08-24"]),
            "Class Type": ["New Hire", "Tenure Conversion"],
            "Session #": ["S-04191", "S-04194"],
        }).to_excel(w, sheet_name="Expert Roster", index=False)


def _write_roster_current(path):
    """Current export format: 'Session #' -> 'Class ID', no 'Class Type' column."""
    with pd.ExcelWriter(path) as w:
        pd.DataFrame({"Note": ["summary sheet is ignored"]}).to_excel(w, sheet_name="Summary", index=False)
        pd.DataFrame({
            "Expert Name": ["Ann A", "Bo B"],
            "EEID": [725820, 598990],
            "Trainer Name": ["T One", "T Two"],
            "Start Date": pd.to_datetime(["2026-08-24", "2026-08-24"]),
            "Class ID": ["2026 5270", "2026 5271"],
        }).to_excel(w, sheet_name="Expert Roster", index=False)


def test_load_class_roster_current_format(tmp_path):
    # New header "Class ID" maps to class_id; dropped "Class Type" is simply absent.
    p = tmp_path / "roster.xlsx"
    _write_roster_current(p)
    r = load_class_roster(p)

    assert "class_type" not in r.columns
    assert list(r.columns) == ["agent_id", "agent_name", "class_id", "trainer",
                               "training_start_date", "icp_client"]
    assert r.set_index("agent_id").loc["725820"]["class_id"] == "2026 5270"   # Class ID -> class_id
    assert r["class_id"].nunique() == 2


def test_load_class_roster_normalizes(tmp_path):
    p = tmp_path / "roster.xlsx"
    _write_roster(p)
    r = load_class_roster(p)

    assert list(r.columns) == ["agent_id", "agent_name", "class_id", "trainer",
                               "training_start_date", "class_type", "icp_client"]
    assert set(r["agent_id"]) == {"725820", "598990"}          # EEID int -> clean string (no "725820.0")
    row = r.set_index("agent_id").loc["725820"]
    assert row["class_id"] == "S-04191"                         # Session # -> class_id
    assert row["trainer"] == "T One & T Two"
    assert row["training_start_date"] == "2026-08-24"          # -> YYYY-MM-DD string
    assert row["class_type"] == "New Hire"
    assert (r["icp_client"] == "training").all()
