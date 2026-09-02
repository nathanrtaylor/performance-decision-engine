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
    assert a1["pace"] == "behind"               # current 2 < expected 3


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
    assert r["primary_block_label"].startswith("Block 2")     # block number surfaced for the trainer
    assert r["reason"].startswith("Block 2")                  # reason leads with the block number
    assert all(tb <= 2 for tb in r["triggered_blocks"])       # never a future/locked block
    assert set(r["groups"].keys()) == {"learning", "training_support", "coaching"}
    assert r["groups"]["learning"]["actor"] == "Expert"
    assert r["focus_skills"] == ["Solve"]


def test_clean_expert_is_completed_and_on_track():
    recs, _ = _records()
    a2 = recs["a2"]
    assert a2["remediation"] is None
    assert a2["on_track"] is True               # current 3 >= expected 3
    assert a2["pace"] == "on_track"             # current 3 == expected 3
    assert a2["status"] == "completed"          # reached last block, no deficiency


def test_action_groups_labels_and_actors():
    g = build_action_groups("Device Basics", ["Ask for the Sale", "Price Statement"])
    assert g["learning"]["label"] == "Learning actions"
    assert g["training_support"]["label"] == "Training support actions"
    assert g["coaching"]["label"] == "Coaching actions"
    assert [g[k]["actor"] for k in ("learning", "training_support", "coaching")] == ["Expert", "Trainer", "Coach"]
    assert all(g[k]["actions"] for k in g)      # each has at least one action line


def test_roster_expert_without_skill_data_is_not_started():
    # Roster is the source of truth: an expert on the roster with no skill data still appears.
    agents = pd.concat([_agents_df(), pd.DataFrame([{
        "agent_id": "a3", "agent_name": "Cy", "class_id": "C2", "trainer": "T",
        "icp_client": "training", "training_start_date": "2026-01-01"}])], ignore_index=True)
    recs, _ = build_training_records(_skills_df(), agents, _program(), RemediationPolicy(),
                                     _SKILL_META, report_date="2026-01-06", pass_mark=0.80)
    by = {r["id"]: r for r in recs}
    assert "a3" in by                            # appears despite no skills in skills_df
    assert by["a3"]["status"] == "not_started"
    assert by["a3"]["remediation"] is None


def test_locked_block_stays_blank_even_when_it_shares_a_skill():
    # Block 3 (locked, not yet reached) develops the same skill as block 1 (reached). The
    # expert has data for that skill, but it must NOT surface under the locked block.
    prog = Program(name="P", pace_hours_per_day=6.0, blocks=[
        Block(id="b1", order=1, label="Stage 1: Greeting", develops=["greet"], expected_completion_day=1),
        Block(id="b2", order=2, label="Stage 2: Solve", develops=["solve"], expected_completion_day=3),
        Block(id="b3", order=3, label="Stage 3: Re-greet", develops=["greet"], expected_completion_day=5),
    ])
    skills = pd.DataFrame([{"agent_id": "a1", "metric": "greet", "value": 0.90, "benchmark": 0.80}])
    agents = pd.DataFrame([{"agent_id": "a1", "agent_name": "Ann", "class_id": "C1", "trainer": "T",
                            "icp_client": "training", "training_start_date": "2026-01-01",
                            "current_block_order": 1}])
    recs, _ = build_training_records(skills, agents, prog, RemediationPolicy(), _SKILL_META,
                                     report_date="2026-01-06", pass_mark=0.80)
    blocks = {b["num"]: b for b in recs[0]["blocks"]}
    assert blocks[1]["status"] == "in_progress" and [s["skill"] for s in blocks[1]["skills"]] == ["greet"]
    assert blocks[3]["status"] == "locked" and blocks[3]["skills"] == []   # blank, despite sharing "greet"


def test_progress_feed_without_skill_data_is_in_training_not_not_started():
    # An expert with a progress feed (current_block_order) but no skill data yet has clearly
    # started -> "in_training" (awaiting skill signal), NOT "not_started".
    agents = pd.DataFrame([{
        "agent_id": "a9", "agent_name": "Di", "class_id": "C3", "trainer": "T",
        "icp_client": "training", "training_start_date": "2026-01-01", "current_block_order": 1}])
    recs, _ = build_training_records(pd.DataFrame(columns=["agent_id", "metric", "value", "benchmark"]),
                                     agents, _program(), RemediationPolicy(), _SKILL_META,
                                     report_date="2026-01-06", pass_mark=0.80)
    by = {r["id"]: r for r in recs}
    assert by["a9"]["status"] == "in_training"
    assert by["a9"]["current_block"]["num"] == 1


def test_no_progress_feed_is_not_schedule_inferred():
    # Without a progress feed (no current_block_order), progress is NOT inferred from the
    # schedule: current block is unknown, pace is pending, blocks are never marked "passed".
    agents = _agents_df().drop(columns=["current_block_order"])
    recs, _ = build_training_records(_skills_df(), agents, _program(), RemediationPolicy(),
                                     _SKILL_META, report_date="2026-01-06", pass_mark=0.80)
    by = {r["id"]: r for r in recs}
    a1, a2 = by["a1"], by["a2"]
    assert a1["current_block"]["num"] is None and a1["pace"] is None
    assert all(b["status"] in ("retraining", "not_tracked") for b in a1["blocks"])  # never "passed"
    assert a1["status"] == "retraining"          # has a below-mark skill (solve)
    assert a2["status"] == "in_training"         # has data, no deficiency, progress not tracked
    assert a2["expected_block_num"] is not None  # expected is still computed (for comparison)
