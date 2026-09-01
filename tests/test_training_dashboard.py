"""Tests for the Phase 4 training dashboard data layer + remediation narratives."""
from __future__ import annotations

import pandas as pd

from cde.training.program import Block, Program, RemediationPolicy
from cde.reporting.training_dashboard import build_training_records, short_desc
from cde.explainability.training_templates import build_action_groups


def _program() -> Program:
    return Program(name="Prog", pace_hours_per_day=6.0, blocks=[
        Block(id="b1", order=1, label="Stage 1: Greeting", develops=["greet"], expected_completion_day=1),
        Block(id="b2", order=2, label="Stage 2: Solve & Test - Device", develops=["solve"], expected_completion_day=3),
        Block(id="b3", order=3, label="Stage 3: Close", develops=["close"], expected_completion_day=5),
    ])


_SKILL_META = {"greet": {"label": "Warm Greeting", "category": "Connection"},
               "solve": {"label": "Solve", "category": "Resolution"},
               "close": {"label": "Close", "category": "Call Close"}}


def _skills_df():
    return pd.DataFrame([
        {"agent_id": "a1", "metric": "greet", "value": 0.90, "benchmark": 0.80},
        {"agent_id": "a1", "metric": "solve", "value": 0.50, "benchmark": 0.80},   # below
        {"agent_id": "a2", "metric": "greet", "value": 0.95, "benchmark": 0.80},
        {"agent_id": "a2", "metric": "solve", "value": 0.88, "benchmark": 0.80},
        {"agent_id": "a2", "metric": "close", "value": 0.90, "benchmark": 0.80},
    ])


def _agents_df():
    return pd.DataFrame([
        {"agent_id": "a1", "agent_name": "Ann", "class_id": "C1", "trainer": "T",
         "icp_client": "training", "training_start_date": "2026-01-01", "current_block_order": 2},
        {"agent_id": "a2", "agent_name": "Bo", "class_id": "C1", "trainer": "T",
         "icp_client": "training", "training_start_date": "2026-01-01", "current_block_order": 3},
    ])


def _records():
    recs, meta = build_training_records(_skills_df(), _agents_df(), _program(),
                                        RemediationPolicy(), _SKILL_META,
                                        report_date="2026-01-06", pass_mark=0.80)
    return {r["id"]: r for r in recs}, meta


def test_short_desc_takes_part_after_colon():
    assert short_desc("Stage 2: Solve & Test - Device") == "Solve & Test - Device"
    assert short_desc("Welcome to Asurion") == "Welcome to Asurion"


def test_meta_carries_program_and_blocks():
    _, meta = _records()
    assert meta["program"] == "Prog" and meta["pass_mark"] == 0.80
    assert [b["num"] for b in meta["blocks"]] == [1, 2, 3]


def test_current_block_and_pacing():
    recs, _ = _records()
    a1 = recs["a1"]
    assert a1["current_block"]["num"] == 2
    assert a1["days_since_start"] == 5
    assert a1["expected_block_num"] == 3        # blocks due by day 5: 1,3,5 -> max order 3
    assert a1["on_track"] is False              # current 2 < expected 3


def test_skills_rolled_up_under_blocks():
    recs, _ = _records()
    a1 = recs["a1"]
    b2 = next(b for b in a1["blocks"] if b["num"] == 2)
    assert b2["status"] == "retraining"         # current block with a below-mark skill
    solve = next(s for s in b2["skills"] if s["skill"] == "solve")
    assert solve["below"] is True and solve["label"] == "Solve"
    # a passed earlier block is marked passed
    assert next(b for b in a1["blocks"] if b["num"] == 1)["status"] == "passed"


def test_remediation_targets_reached_blocks_and_three_action_groups():
    recs, _ = _records()
    r = recs["a1"]["remediation"]
    assert r is not None
    assert r["primary_block"] == 2
    assert all(tb <= 2 for tb in r["triggered_blocks"])       # never a future/locked block
    assert set(r["groups"].keys()) == {"learning", "training_support", "coaching"}
    assert r["groups"]["learning"]["actor"] == "Expert"
    assert r["focus_skills"] == ["Solve"]


def test_clean_expert_is_completed_and_on_track():
    recs, _ = _records()
    a2 = recs["a2"]
    assert a2["remediation"] is None
    assert a2["on_track"] is True               # current 3 >= expected 3
    assert a2["status"] == "completed"          # reached last block, no deficiency


def test_action_groups_labels_and_actors():
    g = build_action_groups("Device Basics", ["Ask for the Sale", "Price Statement"])
    assert g["learning"]["label"] == "Learning actions"
    assert g["training_support"]["label"] == "Training support actions"
    assert g["coaching"]["label"] == "Coaching actions"
    assert [g[k]["actor"] for k in ("learning", "training_support", "coaching")] == ["Expert", "Trainer", "Coach"]
    assert all(g[k]["actions"] for k in g)      # each has at least one action line
