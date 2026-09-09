"""Tests for the Phase 4 training dashboard data layer + remediation narratives."""
from __future__ import annotations

import pandas as pd

from pde.training.program import Block, Component, Program, RemediationPolicy
from pde.reporting.training_dashboard import build_training_records, short_desc
from pde.explainability.training_templates import build_action_groups


def _program() -> Program:
    # Each block carries one skill_sim component so activity-based completion can be exercised.
    return Program(name="Prog", pace_hours_per_day=6.0, blocks=[
        Block(id="b1", order=1, label="Stage 1: Greeting", develops=["greet"], expected_completion_day=1,
              components=[Component(kind="skill_sim", ref="SIM1")]),
        Block(id="b2", order=2, label="Stage 2: Solve & Test - Device", develops=["solve"], expected_completion_day=3,
              components=[Component(kind="skill_sim", ref="SIM2")]),
        Block(id="b3", order=3, label="Stage 3: Close", develops=["close"], expected_completion_day=5,
              components=[Component(kind="skill_sim", ref="SIM3")]),
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
    # No current_block_order: the current block is inferred from activity, never the roster.
    return pd.DataFrame([
        {"agent_id": "a1", "agent_name": "Ann", "class_id": "C1", "trainer": "T",
         "icp_client": "training", "training_start_date": "2026-01-01"},
        {"agent_id": "a2", "agent_name": "Bo", "class_id": "C1", "trainer": "T",
         "icp_client": "training", "training_start_date": "2026-01-01"},
    ])


def _sims_df():
    # Activity drives the inferred current block: a1 reaches block 2; a2 reaches and passes all 3.
    rows = [("a1", 1, "SIM1", 0.9), ("a1", 2, "SIM2", 0.9),
            ("a2", 1, "SIM1", 0.9), ("a2", 2, "SIM2", 0.9), ("a2", 3, "SIM3", 0.9)]
    return pd.DataFrame([
        {"agent_id": a, "challenge_id": ref, "label": ref, "sim_id": ref,
         "block_num": bn, "sessions": 2, "pass_rate": pr, "last_period": "2026-01-05"}
        for (a, bn, ref, pr) in rows])


def _records():
    recs, meta = build_training_records(_skills_df(), _agents_df(), _program(),
                                        RemediationPolicy(), _SKILL_META,
                                        report_date="2026-01-06", pass_mark=0.80,
                                        sims_taken=_sims_df())
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


def test_coaching_history_is_attached_and_roster_bounded():
    # "Recent coaching history" block: reuse the coaching frame, but bounded to the roster.
    ch = pd.DataFrame([
        {"agent_id": "a1", "coaching_date": "2026-08-10", "coaching_type": "ADAPT",
         "behavior_selected": "Show Compassion", "coaching_status": "Submitted"},
        {"agent_id": "a1", "coaching_date": "2026-08-15", "coaching_type": "Growth Plan",
         "behavior_selected": "Drive Results", "coaching_status": "Submitted"},
        {"agent_id": "zzz", "coaching_date": "2026-08-01", "coaching_type": "ADAPT",   # NOT on the roster
         "behavior_selected": "Off Roster", "coaching_status": "Submitted"},
    ])
    recs, meta = build_training_records(_skills_df(), _agents_df(), _program(), RemediationPolicy(),
                                        _SKILL_META, report_date="2026-01-06", pass_mark=0.80,
                                        coaching_history=ch)
    by = {r["id"]: r for r in recs}
    assert meta["schema_version"] == "1.4"
    # a1 gets its events, newest-first; keys are the compact {ty,tp,dt,st}
    assert [h["dt"] for h in by["a1"]["hist"]] == ["2026-08-15", "2026-08-10"]
    assert by["a1"]["hist"][0] == {"ty": "Growth Plan", "tp": "Drive Results",
                                   "dt": "2026-08-15", "st": "Submitted"}
    assert by["a2"]["hist"] == []                 # roster expert with no coaching history
    assert "zzz" not in by                         # off-roster agent never becomes a record
    # default (no coaching_history) -> empty hist, no error
    recs2, _ = build_training_records(_skills_df(), _agents_df(), _program(), RemediationPolicy(),
                                      _SKILL_META, report_date="2026-01-06", pass_mark=0.80)
    assert all(r["hist"] == [] for r in recs2)


def test_sims_taken_nest_under_block_and_cbts_attach():
    # A sim with a block_num nests under that block; one without lands in sims_unmapped;
    # CBTs attach to the record. Default (no frames) -> empty lists, backward compatible.
    sims = pd.DataFrame([
        {"agent_id": "a1", "challenge_id": "judy_terry", "label": "Expert Workspace",
         "sim_id": "ASC-SIM-6J5TR3", "block_num": 2, "sessions": 3, "pass_rate": 0.62,
         "last_period": "2026-01-05"},
        {"agent_id": "a1", "challenge_id": "becky_bergen", "label": "Becky Bergen",
         "sim_id": None, "block_num": None, "sessions": 5, "pass_rate": 0.80,
         "last_period": "2026-01-04"},
    ])
    cbts = pd.DataFrame([
        # a CBT mapped to block 2 nests under it; an unmapped one (block_num None) is dropped.
        {"agent_id": "a1", "courseid": "cbt_x", "coursename": "Device Basics CBT",
         "block_num": 2, "sessions": 1, "pass_rate": 0.0, "last_period": "2026-01-03"},
        {"agent_id": "a1", "courseid": "cbt_y", "coursename": "Unmapped Course",
         "block_num": None, "sessions": 1, "pass_rate": 1.0, "last_period": "2026-01-02"},
    ])
    recs, meta = build_training_records(_skills_df(), _agents_df(), _program(), RemediationPolicy(),
                                        _SKILL_META, report_date="2026-01-06", pass_mark=0.80,
                                        sims_taken=sims, cbts_taken=cbts)
    a1 = {r["id"]: r for r in recs}["a1"]
    assert meta["schema_version"] == "1.4"
    b2 = {b["num"]: b for b in a1["blocks"]}[2]
    assert [s["sim_id"] for s in b2["sims"]] == ["ASC-SIM-6J5TR3"]
    assert [s["label"] for s in a1["sims_unmapped"]] == ["Becky Bergen"]
    # CBTs nest under their block; there is no record-level cbts catch-all (unmapped are dropped).
    assert [c["coursename"] for c in b2["cbts"]] == ["Device Basics CBT"]
    assert "cbts" not in a1
    all_block_cbts = [c["coursename"] for b in a1["blocks"] for c in b["cbts"]]
    assert "Unmapped Course" not in all_block_cbts
    # backward compatible: no frames -> empty lists, no error
    recs2, _ = build_training_records(_skills_df(), _agents_df(), _program(), RemediationPolicy(),
                                      _SKILL_META, report_date="2026-01-06", pass_mark=0.80)
    assert all(r["sims_unmapped"] == [] for r in recs2)
    assert all(all(b["sims"] == [] and b["cbts"] == [] for b in r["blocks"]) for r in recs2)


def test_future_block_skill_hidden_by_activity_ceiling():
    # No progress feed. The expert's only scored skill ("close") is developed by block 3, but their
    # crosswalked activity only reaches block 1 -> the skill is ignored (not surfaced, not counted)
    # until they reach block 3. With no crosswalked activity, there is no ceiling and it surfaces.
    prog = _program()  # b1 develops greet, b2 solve, b3 close
    skills = pd.DataFrame([{"agent_id": "c1", "metric": "close", "value": 0.90, "benchmark": 0.80}])
    agents = pd.DataFrame([{"agent_id": "c1", "agent_name": "Cy", "class_id": "C1", "trainer": "T",
                            "icp_client": "training", "training_start_date": "2026-01-01"}])  # no feed
    sims = pd.DataFrame([{"agent_id": "c1", "challenge_id": "x", "label": "X", "sim_id": None,
                          "block_num": 1, "sessions": 1, "pass_rate": 0.9, "last_period": "2026-01-05"}])
    recs, _ = build_training_records(skills, agents, prog, RemediationPolicy(), _SKILL_META,
                                     report_date="2026-01-06", pass_mark=0.80, sims_taken=sims)
    blk = {b["num"]: b for b in recs[0]["blocks"]}
    assert blk[3]["skills"] == []          # 'close' (block 3) hidden: activity ceiling is block 1
    assert recs[0]["avg_skill"] is None    # its only skill is a future-block skill -> ignored
    # control: no crosswalked activity -> unbounded -> skill surfaces under its curriculum block
    recs2, _ = build_training_records(skills, agents, prog, RemediationPolicy(), _SKILL_META,
                                      report_date="2026-01-06", pass_mark=0.80)
    blk2 = {b["num"]: b for b in recs2[0]["blocks"]}
    assert [s["skill"] for s in blk2[3]["skills"]] == ["close"]
    assert recs2[0]["avg_skill"] == 0.9


def test_locked_block_stays_blank_even_when_it_shares_a_skill():
    # Block 3 (locked, not yet reached) develops the same skill as block 1 (reached). The
    # expert has data for that skill, but it must NOT surface under the locked block.
    prog = Program(name="P", pace_hours_per_day=6.0, blocks=[
        Block(id="b1", order=1, label="Stage 1: Greeting", develops=["greet"], expected_completion_day=1,
              components=[Component(kind="skill_sim", ref="SIM1")]),
        Block(id="b2", order=2, label="Stage 2: Solve", develops=["solve"], expected_completion_day=3,
              components=[Component(kind="skill_sim", ref="SIM2")]),
        Block(id="b3", order=3, label="Stage 3: Re-greet", develops=["greet"], expected_completion_day=5,
              components=[Component(kind="skill_sim", ref="SIM3")]),
    ])
    skills = pd.DataFrame([{"agent_id": "a1", "metric": "greet", "value": 0.90, "benchmark": 0.80}])
    agents = pd.DataFrame([{"agent_id": "a1", "agent_name": "Ann", "class_id": "C1", "trainer": "T",
                            "icp_client": "training", "training_start_date": "2026-01-01"}])
    # Activity reaches only block 1 -> block 3 is beyond the ceiling (locked).
    sims = pd.DataFrame([{"agent_id": "a1", "challenge_id": "SIM1", "label": "G", "sim_id": "SIM1",
                          "block_num": 1, "sessions": 1, "pass_rate": 0.9, "last_period": "2026-01-05"}])
    recs, _ = build_training_records(skills, agents, prog, RemediationPolicy(), _SKILL_META,
                                     report_date="2026-01-06", pass_mark=0.80, sims_taken=sims)
    blocks = {b["num"]: b for b in recs[0]["blocks"]}
    assert blocks[1]["status"] == "passed" and [s["skill"] for s in blocks[1]["skills"]] == ["greet"]
    assert blocks[3]["status"] == "locked" and blocks[3]["skills"] == []   # blank, despite sharing "greet"


def test_activity_without_skill_data_is_in_training_not_not_started():
    # An expert with crosswalked activity (a reached block) but no skill signal yet has clearly
    # started -> "in_training" (awaiting skill signal), NOT "not_started".
    agents = pd.DataFrame([{
        "agent_id": "a9", "agent_name": "Di", "class_id": "C3", "trainer": "T",
        "icp_client": "training", "training_start_date": "2026-01-01"}])
    sims = pd.DataFrame([{"agent_id": "a9", "challenge_id": "SIM1", "label": "G", "sim_id": "SIM1",
                          "block_num": 1, "sessions": 1, "pass_rate": 0.9, "last_period": "2026-01-05"}])
    recs, _ = build_training_records(pd.DataFrame(columns=["agent_id", "metric", "value", "benchmark"]),
                                     agents, _program(), RemediationPolicy(), _SKILL_META,
                                     report_date="2026-01-06", pass_mark=0.80, sims_taken=sims)
    by = {r["id"]: r for r in recs}
    assert by["a9"]["status"] == "in_training"
    assert by["a9"]["current_block"]["num"] == 1


def test_no_activity_means_no_current_block_or_pace():
    # With no crosswalked activity, the current block is unknown and pace is pending; blocks are
    # never marked "passed" (completion is activity-inferred). Remediation still flags below-mark skills.
    recs, _ = build_training_records(_skills_df(), _agents_df(), _program(), RemediationPolicy(),
                                     _SKILL_META, report_date="2026-01-06", pass_mark=0.80)  # no sims
    by = {r["id"]: r for r in recs}
    a1, a2 = by["a1"], by["a2"]
    assert a1["current_block"]["num"] is None and a1["pace"] is None
    assert all(b["status"] in ("retraining", "not_tracked") for b in a1["blocks"])  # never "passed"
    assert a1["status"] == "retraining"          # has a below-mark skill (solve)
    assert a2["status"] == "in_training"         # has data, no deficiency, no activity tracked
    assert a2["expected_block_num"] is not None  # expected is still computed (for comparison)
