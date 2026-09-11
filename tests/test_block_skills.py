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


def test_block_skills_core_vs_surfaced_and_latest(tmp_path):
    raw = _write(tmp_path, [
        # sim a -> block 1 (develops warm_greeting): greeting is CORE here, scope is SURFACED (block 2's)
        ["sa1", "a1", "2026-01-01 10:00:00", "2026-01-01", "all", "a", "greeting", 1],
        ["sa1", "a1", "2026-01-01 10:00:00", "2026-01-01", "all", "a", "scope", 0],
        # sim b -> block 2 (develops scope), three attempts; scope improves 0,0,1 -> LATEST = 1 (mean would be 0.33)
        ["sb1", "a1", "2026-01-02 09:00:00", "2026-01-02", "all", "b", "scope", 0],
        ["sb1", "a1", "2026-01-02 09:00:00", "2026-01-02", "all", "b", "greeting", 1],
        ["sb2", "a1", "2026-01-02 12:00:00", "2026-01-02", "all", "b", "scope", 0],
        ["sb2", "a1", "2026-01-02 12:00:00", "2026-01-02", "all", "b", "greeting", 1],
        ["sb3", "a1", "2026-01-02 15:00:00", "2026-01-02", "all", "b", "scope", 1],
        ["sb3", "a1", "2026-01-02 15:00:00", "2026-01-02", "all", "b", "greeting", 1],
        # sim c -> non-ASCEND, dropped entirely
        ["sc1", "a1", "2026-01-03 09:00:00", "2026-01-03", "all", "c", "scope", 0],
    ])
    out = _build_block_skills(raw, _program(), _PROFILES, pass_mark=0.80)
    got = {(int(r.block_num), r.skill): (round(float(r.value), 3), bool(r.below), bool(r.core))
           for r in out.itertuples()}

    # block 1 develops warm_greeting -> greeting CORE; scope is SURFACED here (no longer dropped), shown w/ value+below
    assert got[(1, "warm_greeting")] == (1.0, False, True)
    assert got[(1, "scope")] == (0.0, True, False)
    # block 2 develops scope -> scope CORE (LATEST attempt = 1.0, not the 0.33 mean); warm_greeting SURFACED
    assert got[(2, "scope")] == (1.0, False, True)
    assert got[(2, "warm_greeting")] == (1.0, False, False)
    # nothing dropped by an asymmetry filter now; non-ASCEND sim c contributes nothing
    assert len(out) == 4


def test_block_skills_below_flag_and_missing_file(tmp_path):
    # latest attempt missed the behavior -> below-mark
    raw = _write(tmp_path, [
        ["sb1", "a1", "2026-01-02 09:00:00", "2026-01-02", "all", "b", "scope", 1],
        ["sb2", "a1", "2026-01-02 15:00:00", "2026-01-02", "all", "b", "scope", 0],   # latest = missed
    ])
    out = _build_block_skills(raw, _program(), _PROFILES, pass_mark=0.80)
    row = out[(out.block_num == 2) & (out.skill == "scope")].iloc[0]
    assert row.value == 0.0 and bool(row.below) is True and bool(row.core) is True   # scope is core for block 2

    # no behaviors file -> empty frame, correct columns
    empty = _build_block_skills(tmp_path / "nope", _program(), _PROFILES)
    assert list(empty.columns) == ["agent_id", "block_num", "skill", "value", "below", "core"] and empty.empty
