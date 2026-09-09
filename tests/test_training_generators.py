"""Tests for the training-config generators + shared helpers.

Covers the behaviors that landed with the CBT mapping / activity work and are most likely to
regress silently: the shared cbt_<id> key, the WI-5 SIM-ID typo fix, curriculum CBT parsing
(GUID vs name-slug vs live-extract backfill), the scored-only CBT catalog filter, and
benchmark preservation on regeneration.
"""
from __future__ import annotations

import pandas as pd

import tools.gen_program_components as gpc
import tools.gen_training_configs as gtc
from pde.training.program import cbt_metric_key


# --- shared key + WI-5 normalization ---------------------------------------
def test_cbt_metric_key_normalizes():
    assert cbt_metric_key("ABC-123 xY") == "cbt_abc_123_xy"
    assert cbt_metric_key("73acee92da1810016b1d463f83fc0000") == "cbt_73acee92da1810016b1d463f83fc0000"


def test_sim_ref_fixes_wi5_typo():
    assert gpc._sim_ref("ASC-ASC-SIM-3JEJ0P") == "ASC-SIM-3JEJ0P"   # WI-5 spreadsheet typo
    assert gpc._sim_ref("ASC-SIM-6J5TR3") == "ASC-SIM-6J5TR3"       # already clean, untouched
    assert gpc._sim_ref(None) is None


# --- curriculum CBT parsing -------------------------------------------------
_CURRIC_COLS = ["Learning Block", "Duration (Minutes)", "Course", "Modality", "Workday Learning Link", "Resource"]


def _curric(rows, tmp_path):
    p = tmp_path / "curric.csv"
    pd.DataFrame(rows, columns=_CURRIC_COLS).to_csv(p, index=False)
    return p


def test_cbt_components_ref_from_course_guid(tmp_path):
    guid = "64844428d3b0100109bde109fd2d0001"
    p = _curric([["Learning Block 03", "10", "Asurion_ASCND_All_Helix Search", "CBT",
                  f"https://wd5.myworkday.com/asurion/learning/course/{guid}", ""]], tmp_path)
    out = gpc._cbt_components_by_block(p, courseid_by_name={})
    assert out[3] == [{"kind": "cbt", "ref": f"cbt_{guid}", "topic": "Asurion_ASCND_All_Helix Search",
                       "modality": "CBT", "develops": []}]


def test_cbt_components_backfill_courseid_from_live_extract(tmp_path):
    # A /d/inst/ link carries no GUID; the courseid is recovered by coursename match.
    p = _curric([["Learning Block 01", "10", "Asurion_All_CPNI and Asurion", "CBT",
                  "https://wd5.myworkday.com/asurion/d/inst/1$17816/17816$407.htmld", ""]], tmp_path)
    by_name = {gpc._norm_name("Asurion_All_CPNI and Asurion"): "638adfafc54610016ad537fc10560000"}
    out = gpc._cbt_components_by_block(p, by_name)
    assert out[1][0]["ref"] == "cbt_638adfafc54610016ad537fc10560000"


def test_cbt_components_name_slug_when_no_courseid(tmp_path):
    p = _curric([["Learning Block 10", "15", "Soluto_ASCND_VZW_Network Concerns", "CBT", "", ""]], tmp_path)
    out = gpc._cbt_components_by_block(p, courseid_by_name={})
    assert out[10][0]["ref"] == "soluto_ascnd_vzw_network_concerns"     # curriculum-only slug


def test_cbt_components_ignores_non_cbt_rows(tmp_path):
    p = _curric([
        ["Learning Block 02", "7", "Some Video", "Video", "", ""],
        ["Learning Block 02", "2", "A Simulation", "Simulation", "", ""],
        ["Learning Block 02", "15", "Real CBT", "CBT", "", ""],
    ], tmp_path)
    out = gpc._cbt_components_by_block(p, courseid_by_name={})
    assert list(out.keys()) == [2] and len(out[2]) == 1 and out[2][0]["topic"] == "Real CBT"


# --- scored-only CBT catalog filter ----------------------------------------
def test_cbt_metrics_only_scored_assessments(tmp_path):
    # scored course has a calc>0 somewhere; completion-only is always 0 -> not a metric.
    pd.DataFrame([
        {"courseid": "scored1", "coursename": "Scored Assessment", "calc": 0.0},
        {"courseid": "scored1", "coursename": "Scored Assessment", "calc": 0.9},
        {"courseid": "compl1", "coursename": "Completion Only", "calc": 0.0},
    ]).to_csv(tmp_path / "training_cbt.csv", index=False)
    metrics, cats = gtc._cbt_metrics(tmp_path)
    assert set(metrics) == {"cbt_scored1"}          # completion-only excluded
    assert cats == ["cbt"]


# --- benchmark preservation on regeneration --------------------------------
def test_merge_benchmarks_preserves_existing_seeds_new():
    existing = {"cbt_a": {"default": 0.9}}            # hand-tuned
    merged = gtc._merge_benchmarks(existing, ["cbt_a", "cbt_b"], pass_mark=0.8)
    assert merged["cbt_a"] == {"default": 0.9}        # preserved
    assert merged["cbt_b"] == {"default": 0.8}        # new -> seeded at default
