"""Unit tests for the training program structure + remediation logic (Phase 3).

Uses synthetic fixtures so the logic is validated independent of real gate/CBT data.
"""
from __future__ import annotations

from pde.training.program import (
    Block, Component, Program, RemediationPolicy,
    build_skill_routing, program_coverage, evaluate_block_gates,
    expected_completion_day, behind_schedule_blocks, plan_remediation,
    blocks_implicated_by_skills, load_program, load_policy,
    skills_observed_before_taught,
)


def test_skills_observed_before_taught_flags_early_and_skips_untaught():
    prog = Program(name="P", blocks=[
        Block(id="b1", order=1, label="B1", develops=["greet"]),
        Block(id="b2", order=2, label="B2", develops=["solve"]),
        Block(id="b3", order=3, label="B3", develops=["close"]),
    ])
    observed = [("close", 1), ("close", 3), ("greet", 1), ("solve", 2), ("untaught", 1), (None, 2)]
    out = skills_observed_before_taught(prog, observed)
    assert out == {"close": {"taught": 3, "early_blocks": [1]}}   # only 'close' seen (block 1) before taught (block 3)
    # greet/solve observed at/after their taught block -> not flagged; untaught + None -> skipped


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _prog() -> Program:
    b1 = Block(
        id="b1", order=1, label="Greeting",
        develops=["greet", "verify"],
        components=[
            Component(kind="skill_sim", ref="sim_greet", develops=["greet"]),
            Component(kind="cbt", ref="cbt_greet", develops=["greet", "verify"]),
        ],
        gate_test_call="tc1", gate_pass_mark=0.9,
        expected_completion_day=1, estimated_hours=3.0,
    )
    b2 = Block(
        id="b2", order=2, label="Solve",
        develops=["solve"],
        components=[
            Component(kind="skill_sim", ref="sim_solve", develops=["solve"]),
            Component(kind="skill_sim", ref="sim_other", develops=["other"]),
        ],
        gate_test_call="tc2", gate_pass_mark=0.9,
        expected_completion_day=3, estimated_hours=6.0,
    )
    b3 = Block(  # deliberately partial: no gate, no components, no expected day
        id="b3", order=3, label="Close",
        develops=["close"],
        components=[],
        gate_test_call=None, gate_pass_mark=0.9,
        expected_completion_day=None, estimated_hours=None,
    )
    return Program(name="t", blocks=[b1, b2, b3], pace_hours_per_day=6.0, default_gate_pass_mark=0.9)


def _policy(**kw) -> RemediationPolicy:
    base = dict(how_far_back="current_block", skills="deficient_only",
                include_modalities=["skill_sim", "test_call"], once_only=True)
    base.update(kw)
    return RemediationPolicy(**base)


# --------------------------------------------------------------------------- #
# routing + coverage
# --------------------------------------------------------------------------- #
def test_routing_maps_skills_to_blocks_in_order():
    r = build_skill_routing(_prog())
    assert r["greet"] == ["b1"]
    assert r["verify"] == ["b1"]
    assert r["solve"] == ["b2"]
    assert r["close"] == ["b3"]
    assert r["other"] == ["b2"]


def test_coverage_flags_todo_and_unmapped():
    cov = program_coverage(_prog(), catalog_skills={"greet", "verify", "solve", "close", "other", "extra"})
    assert cov["blocks_missing_gate"] == ["b3"]
    assert cov["blocks_missing_components"] == ["b3"]
    assert cov["blocks_missing_expected_day"] == ["b3"]
    assert cov["unmapped_catalog_skills"] == ["extra"]
    assert cov["develops_unknown"] == []


def test_coverage_detects_unknown_develops_skill():
    p = Program(name="t", blocks=[Block(id="x", order=1, label="X", develops=["ghost"])])
    cov = program_coverage(p, catalog_skills={"greet"})
    assert cov["develops_unknown"] == ["ghost"]


# --------------------------------------------------------------------------- #
# gates + pacing
# --------------------------------------------------------------------------- #
def test_evaluate_block_gates_pass_fail_unknown():
    gates = evaluate_block_gates(_prog(), {"tc1": 0.95, "tc2": 0.50})
    assert gates == {"b1": "passed", "b2": "failed", "b3": "unknown"}


def test_gate_unknown_when_score_missing():
    gates = evaluate_block_gates(_prog(), {"tc1": 0.95})   # tc2 absent
    assert gates["b2"] == "unknown"


def test_expected_completion_day_explicit_and_derived():
    p = _prog()
    assert expected_completion_day(p, "b1") == 1          # explicit
    # derived program (no explicit days): hours 3,6,3 -> cum 3,9,12 at pace 6 -> days 1,2,2
    d = Program(name="d", pace_hours_per_day=6.0, blocks=[
        Block(id="d1", order=1, label="", estimated_hours=3.0),
        Block(id="d2", order=2, label="", estimated_hours=6.0),
        Block(id="d3", order=3, label="", estimated_hours=3.0),
    ])
    assert expected_completion_day(d, "d1") == 1
    assert expected_completion_day(d, "d2") == 2
    assert expected_completion_day(d, "d3") == 2


def test_behind_schedule_flags_overdue_uncompleted_blocks():
    p = Program(name="t", blocks=[
        Block(id="b1", order=1, label="", expected_completion_day=1),
        Block(id="b2", order=2, label="", expected_completion_day=3),
        Block(id="b3", order=3, label="", expected_completion_day=5),
    ])
    behind = behind_schedule_blocks(p, days_since_start=4, completed_block_ids={"b1"})
    assert behind == ["b2"]            # b1 done; b3 not yet due


# --------------------------------------------------------------------------- #
# remediation mapping
# --------------------------------------------------------------------------- #
def test_current_block_deficient_only_includes_sim_and_test_call():
    plan = plan_remediation("a1", _prog(), _policy(), deficient_skills=["solve"], triggered_blocks=["b2"])
    kinds = {(t.kind, t.ref) for t in plan.targets}
    assert ("skill_sim", "sim_solve") in kinds
    assert ("test_call", "tc2") in kinds
    assert ("skill_sim", "sim_other") not in kinds     # 'other' not deficient
    assert plan.gate_to_clear == ["b2"]


def test_whole_block_includes_non_deficient_components():
    plan = plan_remediation("a1", _prog(), _policy(skills="whole_block"),
                            deficient_skills=["solve"], triggered_blocks=["b2"])
    refs = {t.ref for t in plan.targets}
    assert {"sim_solve", "sim_other"} <= refs


def test_modality_filter_excludes_test_call():
    plan = plan_remediation("a1", _prog(), _policy(include_modalities=["skill_sim"]),
                            deficient_skills=["solve"], triggered_blocks=["b2"])
    assert all(t.kind != "test_call" for t in plan.targets)


def test_partial_block_falls_back_to_block_level_target():
    plan = plan_remediation("a1", _prog(), _policy(), deficient_skills=["close"], triggered_blocks=["b3"])
    assert len(plan.targets) == 1
    t = plan.targets[0]
    assert t.kind == "block" and t.ref == "b3" and t.reason_skills == ["close"]
    assert plan.gate_to_clear == []                    # b3 has no gate


def test_back_n_pulls_in_preceding_block():
    plan = plan_remediation("a1", _prog(), _policy(how_far_back="back_n", back_n=1),
                            deficient_skills=["solve", "greet"], triggered_blocks=["b2"])
    blocks_hit = {t.block_id for t in plan.targets}
    assert "b1" in blocks_hit and "b2" in blocks_hit   # b2 and one block back


def test_to_skill_origin_routes_to_earliest_teaching_block():
    # b2 also develops 'greet' (origin b1); a greet deficiency should pull in b1.
    p = _prog()
    b2 = p.by_id["b2"]
    p2 = Program(name="t", pace_hours_per_day=6.0, blocks=[
        p.by_id["b1"],
        Block(id="b2", order=2, label="Solve", develops=["solve", "greet"],
              components=b2.components, gate_test_call="tc2", gate_pass_mark=0.9),
        p.by_id["b3"],
    ])
    plan = plan_remediation("a1", p2, _policy(how_far_back="to_skill_origin"),
                            deficient_skills=["greet"], triggered_blocks=["b2"])
    assert "b1" in {t.block_id for t in plan.targets}


def test_once_only_excludes_remediated_material():
    # history contains the block -> its targets are dropped
    plan = plan_remediation("a1", _prog(), _policy(), deficient_skills=["solve"],
                            triggered_blocks=["b2"], history={"b2"})
    assert plan.targets == []
    # history contains a specific component ref -> only that component is dropped
    plan2 = plan_remediation("a1", _prog(), _policy(), deficient_skills=["solve"],
                             triggered_blocks=["b2"], history={"sim_solve"})
    assert ("skill_sim", "sim_solve") not in {(t.kind, t.ref) for t in plan2.targets}
    assert ("test_call", "tc2") in {(t.kind, t.ref) for t in plan2.targets}


def test_fallback_trigger_from_deficient_skills_when_no_gates():
    plan = plan_remediation("a1", _prog(), _policy(), deficient_skills=["solve"])  # no triggered_blocks
    assert plan.triggered_blocks == ["b2"]
    assert any("deficient skills" in n for n in plan.notes)


def test_blocks_implicated_helper_returns_program_order():
    assert blocks_implicated_by_skills(_prog(), {"close", "greet"}) == ["b1", "b3"]


def test_plan_includes_behind_schedule_when_dates_given():
    plan = plan_remediation("a1", _prog(), _policy(), deficient_skills=["solve"],
                            triggered_blocks=["b2"], days_since_start=5, completed_block_ids={"b1"})
    # b2 expected day 3, not completed, 5>3 -> behind
    assert "b2" in plan.behind_schedule


# --------------------------------------------------------------------------- #
# loaders (YAML round-trip + TODO normalization)
# --------------------------------------------------------------------------- #
def test_load_program_and_todo_normalization(tmp_path):
    p = tmp_path / "training_program.yaml"
    p.write_text(
        "program:\n"
        "  name: Demo\n"
        "  pace: { hours_per_day: 5 }\n"
        "  defaults: { gate_pass_mark: 0.8 }\n"
        "  blocks:\n"
        "    - id: b1\n      order: 1\n      label: One\n"
        "      develops: [greet]\n"
        "      gate: { test_call: TODO, pass_mark: 0.9 }\n"
        "      components:\n"
        "        - { kind: skill_sim, ref: TODO, develops: [greet] }\n"
        "    - id: b2\n      order: 2\n      label: Two\n"
        "      gate: { test_call: tc2 }\n",
        encoding="utf-8",
    )
    prog = load_program(p)
    assert prog.name == "Demo" and prog.pace_hours_per_day == 5.0
    b1, b2 = prog.blocks
    assert b1.gate_test_call is None                    # 'TODO' normalized to None
    assert b1.components[0].ref is None                 # 'TODO' normalized to None
    assert b2.gate_test_call == "tc2"
    assert b2.gate_pass_mark == 0.8                     # inherits program default


def test_load_policy(tmp_path):
    p = tmp_path / "remediation.yaml"
    p.write_text(
        "remediation:\n"
        "  gate_pass_mark: 0.85\n"
        "  scope: { how_far_back: back_n, back_n: 2, skills: whole_block, include_modalities: [skill_sim] }\n"
        "  once_only: false\n"
        "  behind_schedule: { enabled: true, grace_days: 1 }\n",
        encoding="utf-8",
    )
    pol = load_policy(p)
    assert pol.how_far_back == "back_n" and pol.back_n == 2
    assert pol.skills == "whole_block" and pol.include_modalities == ["skill_sim"]
    assert pol.once_only is False and pol.behind_schedule_grace_days == 1
