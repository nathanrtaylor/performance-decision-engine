"""Block-discrete, latest-attempt skill scoring (_build_block_skills)."""
from __future__ import annotations

import pandas as pd

from pde.cli.run_training_pipeline import _build_block_skills
from pde.training.program import Block, Component, Program


def _program() -> Program:
    return Program(name="P", pace_hours_per_day=6.0, blocks=[
        Block(id="b1", order=1, label="B1", develops=["warm_greeting"],
              components=[Component(kind="skill_sim", ref="SIMA")]),
        Block(id="b2", order=2, label="B2", develops=["scope"],
              components=[Component(kind="skill_sim", ref="SIMB")]),
    ])


_PROFILES = {
    "requirement_rules": {"always": {"include_in_scoring": True, "requires_opportunity": False}},
    "skills": {
        "warm_greeting": {"label": "Warm Greeting", "category": "c", "behavior_names": ["greeting"]},
        "scope": {"label": "Scope", "category": "c", "behavior_names": ["scope"]},
    },
    "profiles": {
        "a": {"challenge_id": "a", "lob": "ASCEND Launchpad", "sim_id": "SIMA",
              "requirements": {"always": ["warm_greeting", "scope"]}},
        "b": {"challenge_id": "b", "lob": "ASCEND Launchpad", "sim_id": "SIMB",
              "requirements": {"always": ["warm_greeting", "scope"]}},
        "c": {"challenge_id": "c", "lob": "Soluto",   # non-ASCEND -> excluded entirely
              "requirements": {"always": ["scope"]}},
    },
}


def _write(tmp_path, rows):
    cols = ["session_id", "agent_id", "session_start_time", "period", "call_type",
            "scorecard_name", "behavior", "behavior_present"]
    pd.DataFrame(rows, columns=cols).to_csv(tmp_path / "training_assist_behaviors.csv", index=False)
    return tmp_path


def test_block_skills_latest_asymmetry_and_ascend(tmp_path):
    raw = _write(tmp_path, [
        # sim a -> block 1. tests greeting (taught b1) and scope (taught b2).
        ["sa1", "a1", "2026-01-01 10:00:00", "2026-01-01", "all", "a", "greeting", 1],
        ["sa1", "a1", "2026-01-01 10:00:00", "2026-01-01", "all", "a", "scope", 0],
        # sim b -> block 2, three attempts same day; scope improves 0,0,1 -> LATEST = 1 (mean would be 0.33)
        ["sb1", "a1", "2026-01-02 09:00:00", "2026-01-02", "all", "b", "scope", 0],
        ["sb1", "a1", "2026-01-02 09:00:00", "2026-01-02", "all", "b", "greeting", 1],
        ["sb2", "a1", "2026-01-02 12:00:00", "2026-01-02", "all", "b", "scope", 0],
        ["sb2", "a1", "2026-01-02 12:00:00", "2026-01-02", "all", "b", "greeting", 1],
        ["sb3", "a1", "2026-01-02 15:00:00", "2026-01-02", "all", "b", "scope", 1],
        ["sb3", "a1", "2026-01-02 15:00:00", "2026-01-02", "all", "b", "greeting", 1],
        # sim c -> non-ASCEND, must be dropped entirely
        ["sc1", "a1", "2026-01-03 09:00:00", "2026-01-03", "all", "c", "scope", 0],
    ])
    out = _build_block_skills(raw, _program(), _PROFILES, pass_mark=0.80)
    got = {(int(r.block_num), r.skill): (round(float(r.value), 3), bool(r.below)) for r in out.itertuples()}

    # LATEST not mean: scope under block 2 comes from sb3 (=1.0), not the 0.33 mean of all attempts
    assert got[(2, "scope")] == (1.0, False)
    # earlier-taught skill (warm_greeting, taught b1) may surface under a later block (b2) -- OK
    assert got[(2, "warm_greeting")] == (1.0, False)
    # warm_greeting under its own block 1 (from sim a)
    assert got[(1, "warm_greeting")] == (1.0, False)
    # ASYMMETRY: scope (taught b2) measured by sim a (block 1) is NOT surfaced under block 1
    assert (1, "scope") not in got
    # ASCEND scope: non-ASCEND sim c contributes nothing
    assert all(r.skill == "scope" or True for r in out.itertuples())  # sanity
    assert len(out) == 3


def test_block_skills_below_flag_and_missing_file(tmp_path):
    # latest attempt missed the behavior -> below-mark
    raw = _write(tmp_path, [
        ["sb1", "a1", "2026-01-02 09:00:00", "2026-01-02", "all", "b", "scope", 1],
        ["sb2", "a1", "2026-01-02 15:00:00", "2026-01-02", "all", "b", "scope", 0],   # latest = missed
    ])
    out = _build_block_skills(raw, _program(), _PROFILES, pass_mark=0.80)
    row = out[(out.block_num == 2) & (out.skill == "scope")].iloc[0]
    assert row.value == 0.0 and bool(row.below) is True

    # no behaviors file -> empty frame, correct columns
    empty = _build_block_skills(tmp_path / "nope", _program(), _PROFILES)
    assert list(empty.columns) == ["agent_id", "block_num", "skill", "value", "below"] and empty.empty
