"""Tests for the native sim verdict + present/total evidence rollup (run_training_pipeline)."""
from __future__ import annotations

import pandas as pd

from pde.cli.run_training_pipeline import _norm_result, _build_sim_results


def test_norm_result_maps_pass_fail_variants():
    # The observed simulator values: "Cleared" (pass) / "Not cleared" (fail).
    assert _norm_result("Cleared") is True
    assert _norm_result("cleared") is True
    assert _norm_result("Not cleared") is False      # contains "cleared" -> negation must win
    assert _norm_result("NOT CLEARED") is False
    # also tolerant of Pass/Fail-style verdicts
    assert _norm_result("Pass") is True
    assert _norm_result("passed") is True
    assert _norm_result("Fail") is False
    assert _norm_result("failed") is False
    assert _norm_result("") is None
    assert _norm_result(None) is None
    assert _norm_result("in progress") is None      # unknown -> None


def _write_sessions(tmp_path, rows):
    cols = ["session_id", "agent_id", "period", "call_type", "scorecard_name",
            "evaluation_results", "present_behaviors", "max_behaviors"]
    pd.DataFrame(rows, columns=cols).to_csv(tmp_path / "training_assist_sessions.csv", index=False)
    return tmp_path


def test_build_sim_results_any_pass_and_present_ratio(tmp_path):
    raw = _write_sessions(tmp_path, [
        # a1/x: two attempts, "Not cleared" then "Cleared" -> any-pass = passed; ratio = best (9/10)
        ["s1", "a1", "2026-01-01", "all", "x", "Not cleared", 6, 10],
        ["s2", "a1", "2026-01-02", "all", "x", "Cleared", 9, 10],
        # a1/y: all "Not cleared" -> passed False; ratio = best (5/10)
        ["s3", "a1", "2026-01-01", "all", "y", "Not cleared", 5, 10],
        ["s4", "a1", "2026-01-02", "all", "y", "Not cleared", 4, 10],
    ])
    out = _build_sim_results(raw).set_index("challenge_id")
    assert bool(out.loc["x", "passed"]) is True and out.loc["x", "result"] == "Pass"
    assert out.loc["x", "present_ratio"] == 0.9
    assert bool(out.loc["y", "passed"]) is False and out.loc["y", "result"] == "Fail"
    assert out.loc["y", "present_ratio"] == 0.5


def test_build_sim_results_all_unknown_verdict_is_none(tmp_path):
    raw = _write_sessions(tmp_path, [
        ["s1", "a1", "2026-01-01", "all", "z", "In Progress", 3, 10],
    ])
    out = _build_sim_results(raw).set_index("challenge_id")
    passed, result = out.loc["z", "passed"], out.loc["z", "result"]
    assert passed is None or pd.isna(passed)         # no attempt had a mappable verdict
    assert result is None or pd.isna(result)
    assert out.loc["z", "present_ratio"] == 0.3


def test_build_sim_results_missing_file_returns_empty(tmp_path):
    out = _build_sim_results(tmp_path)     # no training_assist_sessions.csv
    assert list(out.columns) == ["agent_id", "challenge_id", "passed", "result", "present_ratio"]
    assert out.empty


def test_sim_attempts_newest_first_and_fields(tmp_path):
    from pde.cli.run_training_pipeline import _sim_attempts
    cols = ["session_id", "agent_id", "session_start_time", "period", "call_type",
            "scorecard_name", "evaluation_results", "present_behaviors", "max_behaviors"]
    pd.DataFrame([
        ["s1", "a1", "2026-01-02 09:00:00", "2026-01-02", "all", "x", "Not cleared", 6, 10],
        ["s2", "a1", "2026-01-02 15:00:00", "2026-01-02", "all", "x", "Cleared", 10, 10],
    ], columns=cols).to_csv(tmp_path / "training_assist_sessions.csv", index=False)
    atts = _sim_attempts(tmp_path)[("a1", "x")]
    assert [a["result"] for a in atts] == ["Cleared", "Not cleared"]   # newest session_start_time first
    assert atts[0]["passed"] is True and atts[0]["present"] == 10 and atts[0]["max"] == 10 and atts[0]["ratio"] == 1.0


def test_cbt_top_line_is_highest_and_attempts_newest_first(tmp_path):
    from pde.cli.run_training_pipeline import _build_cbts_taken
    cols = ["agent_id", "period", "call_type", "courseid", "coursename", "attempts",
            "numerator", "denominator", "calc"]
    pd.DataFrame([
        ["a1", "2026-01-01", "all", "c1", "C1", 1, 1, 1, 0.5],
        ["a1", "2026-01-03", "all", "c1", "C1", 1, 1, 1, 0.9],
    ], columns=cols).to_csv(tmp_path / "training_cbt.csv", index=False)
    row = _build_cbts_taken(tmp_path, {}).iloc[0]
    assert row.pass_rate == 0.9                                   # HIGHEST, not the 0.7 mean
    assert [a["score"] for a in row.attempts] == [0.9, 0.5]       # newest completion-day first
    assert row.sessions == len(row.attempts) == 2                 # top-line count matches detail rows
