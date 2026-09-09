"""Tests for the Phase 4 training dashboard data layer + remediation narratives."""
from __future__ import annotations

import pandas as pd

from pde.training.program import Block, Component, Program, RemediationPolicy
from pde.reporting.training_dashboard import (
    build_training_records, short_desc, block_status, overall_status, component_passed, _meets,
)
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
    assert meta["schema_version"] == "1.7"
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
    assert meta["schema_version"] == "1.7"
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


def test_block_skills_drive_discrete_per_block_deficiency():
    # When block_skills is supplied it is the source of truth for each block's skills: a skill
    # surfaces (and flags a block) ONLY under the block it's provided for -- not via develops.
    prog = _program()  # b1 greet, b2 solve, b3 close
    # a1 reaches block 3; give block-scoped skills: greet OK under b1, close BELOW under b3, nothing on b2.
    sims = pd.DataFrame([{"agent_id": "a1", "challenge_id": f"S{o}", "label": "s", "sim_id": f"S{o}",
                          "block_num": o, "sessions": 1, "pass_rate": 0.9, "last_period": "2026-01-05"}
                         for o in (1, 2, 3)])
    block_skills = pd.DataFrame([
        {"agent_id": "a1", "block_num": 1, "skill": "greet", "value": 0.90, "below": False},
        {"agent_id": "a1", "block_num": 3, "skill": "close", "value": 0.50, "below": True},
    ])
    recs, _ = build_training_records(_skills_df(), _agents_df(), prog, RemediationPolicy(), _SKILL_META,
                                     report_date="2026-01-06", pass_mark=0.80,
                                     sims_taken=sims, block_skills=block_skills)
    a1 = {r["id"]: r for r in recs}["a1"]
    bynum = {b["num"]: b for b in a1["blocks"]}
    # block 2 has NO block_skills rows -> no skills, not flagged (even though _skills_df has 'solve' below)
    assert bynum[2]["skills"] == [] and bynum[2]["status"] != "retraining"
    # block 3 carries the below-mark skill -> retraining there
    assert bynum[3]["status"] == "retraining"
    assert [s["skill"] for s in bynum[3]["skills"] if s["below"]] == ["close"]
    # remediation is discrete: driven by block 3 only, focus = Close
    assert a1["remediation"]["primary_block"] == 3
    assert a1["remediation"]["focus_skills"] == ["Close"]
    # avg_skill is the mean of the block-scoped values shown (0.9, 0.5), not the skills_df means
    assert a1["avg_skill"] == 0.7


def test_native_verdict_governs_block_pass_while_skill_evidence_drives_remediation():
    # The simulator's native Pass/Fail verdict decides whether a sim/block is passed; the
    # per-skill pass-rates remain the evidence that drives remediation (unchanged behavior).
    prog = _program()
    agents = _agents_df()

    def _sim(a, order, ref, passed, ratio, pr=0.99):
        return {"agent_id": a, "challenge_id": ref, "label": ref, "sim_id": ref, "block_num": order,
                "sessions": 1, "pass_rate": pr, "last_period": "2026-01-05",
                "passed": passed, "result": "Pass" if passed else "Fail", "present_ratio": ratio}

    sims = pd.DataFrame([
        # a1 (solve below-mark): every sim PASSES the native verdict -> block-pass would be clean,
        # but the below-mark skill still triggers remediation on its block.
        _sim("a1", 1, "SIM1", True, 0.95), _sim("a1", 2, "SIM2", True, 0.90),
        # a2 (all skills >= 0.80): FAILS the block-2 sim by native verdict despite pass_rate 0.99.
        _sim("a2", 1, "SIM1", True, 0.95), _sim("a2", 2, "SIM2", False, 0.40),
        _sim("a2", 3, "SIM3", True, 0.90),
    ])
    recs, _ = build_training_records(_skills_df(), agents, prog, RemediationPolicy(), _SKILL_META,
                                     report_date="2026-01-06", pass_mark=0.80, sims_taken=sims)
    by = {r["id"]: r for r in recs}

    # a2: native FAIL on block 2 keeps it unpassed even though the computed pass_rate (0.99) clears 0.80,
    # so the expert is NOT "completed" (no skill deficiency, so not "retraining" either).
    a2 = by["a2"]
    assert {b["num"]: b for b in a2["blocks"]}[2]["status"] != "passed"
    assert a2["status"] != "completed"
    assert a2["remediation"] is None                       # verdict fail alone does not target skills

    # a1: below-mark skill (solve) still drives remediation regardless of the passing sim verdict.
    a1 = by["a1"]
    assert a1["remediation"] is not None
    assert a1["remediation"]["focus_skills"] == ["Solve"]


# --------------------------------------------------------------------------- #
# Pure decision helpers (activity-inferred status taxonomy)
# --------------------------------------------------------------------------- #
def test_meets():
    assert _meets(0.9, 0.8) is True
    assert _meets(0.7, 0.8) is False
    assert _meets(None, 0.8) is None and _meets(0.9, None) is None


def test_block_status_ladder():
    # a below-mark skill wins first -- even with no activity -- so the block ladder agrees with the
    # block_defic-driven overall status (previously no-activity blocks all read not_tracked).
    assert block_status(False, None, 2, has_below=True, block_passed=False) == "retraining"
    assert block_status(False, None, 2, has_below=False, block_passed=False) == "not_tracked"
    assert block_status(True, 3, 5, has_below=False, block_passed=False) == "locked"       # beyond reached
    assert block_status(True, 3, 2, has_below=False, block_passed=True) == "passed"
    assert block_status(True, 3, 3, has_below=False, block_passed=False) == "in_progress"
    assert block_status(True, 3, 2, has_below=True, block_passed=True) == "retraining"     # deficiency beats passed


def test_overall_status_cascade():
    assert overall_status(False, False, False, False, None) == "not_started"
    assert overall_status(False, True, False, False, None) == "in_training"
    assert overall_status(True, True, True, False, True) == "retraining"
    assert overall_status(True, True, False, True, True) == "completed"
    assert overall_status(True, True, False, False, False) == "behind"
    assert overall_status(True, True, False, False, True) == "on_track"
    assert overall_status(True, False, False, False, None) == "in_training"   # data, no activity


def test_component_passed():
    cbt = Component(kind="cbt", ref="cbt_x")
    # completion-only CBT: satisfied by completion alone (no score, never a pending 0%)
    assert component_passed(cbt, {"cbt_x": {"scored": False, "completed": True}}, {}, {}, 0.8) is True
    assert component_passed(cbt, {"cbt_x": {"scored": False, "completed": False}}, {}, {}, 0.8) is False
    # scored CBT: vs its benchmark in vals
    assert component_passed(cbt, {"cbt_x": {"scored": True}}, {}, {"cbt_x": (0.9, 0.8)}, 0.8) is True
    assert component_passed(cbt, {"cbt_x": {"scored": True}}, {}, {"cbt_x": (0.5, 0.8)}, 0.8) is False
    assert component_passed(cbt, {}, {}, {}, 0.8) is False                      # no activity
    sim = Component(kind="skill_sim", ref="S1")
    # native Results verdict (`passed`) is authoritative when present -- wins over the pass-rate proxy
    assert component_passed(sim, {}, {"S1": {"passed": True, "pass_rate": 0.1}}, {}, 0.8) is True
    assert component_passed(sim, {}, {"S1": {"passed": False, "pass_rate": 0.99}}, {}, 0.8) is False
    # fallback to the computed pass-rate when there's no native verdict (passed missing / None)
    assert component_passed(sim, {}, {"S1": {"pass_rate": 0.9}}, {}, 0.8) is True
    assert component_passed(sim, {}, {"S1": {"pass_rate": 0.7}}, {}, 0.8) is False
    assert component_passed(sim, {}, {"S1": {"passed": None, "pass_rate": 0.9}}, {}, 0.8) is True


# --------------------------------------------------------------------------- #
# Activity-driven pace + completion-only CBT (integration)
# --------------------------------------------------------------------------- #
def test_pace_behind_and_ahead():
    prog = _program()   # expected_completion_day 1 / 3 / 5
    agents = pd.DataFrame([
        {"agent_id": "beh", "agent_name": "B", "class_id": "C", "trainer": "T",
         "icp_client": "training", "training_start_date": "2026-01-01"},   # 5 days in -> expected block 3
        {"agent_id": "ahd", "agent_name": "A", "class_id": "C", "trainer": "T",
         "icp_client": "training", "training_start_date": "2026-01-05"},   # 1 day in  -> expected block 1
    ])
    skills = pd.DataFrame([{"agent_id": a, "metric": "greet", "value": 0.9, "benchmark": 0.8}
                           for a in ("beh", "ahd")])

    def _sim(a, order):
        return {"agent_id": a, "challenge_id": f"{a}{order}", "label": "s", "sim_id": f"{a}{order}",
                "block_num": order, "sessions": 1, "pass_rate": 0.9, "last_period": "2026-01-05"}
    sims = pd.DataFrame([_sim("beh", 1)] + [_sim("ahd", o) for o in (1, 2, 3)])
    recs, _ = build_training_records(skills, agents, prog, RemediationPolicy(), _SKILL_META,
                                     report_date="2026-01-06", pass_mark=0.80, sims_taken=sims)
    by = {r["id"]: r for r in recs}
    assert by["beh"]["current_block"]["num"] == 1 and by["beh"]["expected_block_num"] == 3
    assert by["beh"]["pace"] == "behind" and by["beh"]["status"] == "behind"
    assert by["ahd"]["current_block"]["num"] == 3 and by["ahd"]["expected_block_num"] == 1
    assert by["ahd"]["pace"] == "ahead"


def test_completion_only_cbt_passes_block_but_scored_below_does_not():
    prog = Program(name="P", pace_hours_per_day=6.0, blocks=[
        Block(id="b1", order=1, label="B1", expected_completion_day=1,
              components=[Component(kind="cbt", ref="cbt_c1")]),
    ])
    agents = pd.DataFrame([{"agent_id": "z", "agent_name": "Z", "class_id": "C", "trainer": "T",
                            "icp_client": "training", "training_start_date": "2026-01-01"}])
    base_cbt = {"agent_id": "z", "courseid": "c1", "coursename": "C1", "ref": "cbt_c1",
                "block_num": 1, "sessions": 1, "last_period": "2026-01-05"}
    # completion-only: no score, satisfied by completion -> block passed (and no phantom pending score)
    cbts = pd.DataFrame([{**base_cbt, "scored": False, "completed": True, "pass_rate": None}])
    recs, _ = build_training_records(pd.DataFrame(columns=["agent_id", "metric", "value", "benchmark"]),
                                     agents, prog, RemediationPolicy(), {}, report_date="2026-01-06",
                                     pass_mark=0.80, cbts_taken=cbts)
    b1 = {b["num"]: b for b in recs[0]["blocks"]}[1]
    assert b1["status"] == "passed" and b1["cbts"][0]["scored"] is False and b1["cbts"][0]["pass_rate"] is None
    # scored but below its benchmark -> component not satisfied -> block not passed
    cbts2 = pd.DataFrame([{**base_cbt, "scored": True, "completed": True, "pass_rate": 0.5}])
    skills2 = pd.DataFrame([{"agent_id": "z", "metric": "cbt_c1", "value": 0.5, "benchmark": 0.8}])
    recs2, _ = build_training_records(skills2, agents, prog, RemediationPolicy(), {},
                                      report_date="2026-01-06", pass_mark=0.80, cbts_taken=cbts2)
    assert {b["num"]: b for b in recs2[0]["blocks"]}[1]["status"] == "in_progress"
