"""The training extract includes a coaching_history output; it must render (DB-free).

Verifies that the coaching_history SQL template compiles cleanly in the *training* extract
context (globals + the coaching_history output's params), so a real training extract run
produces coaching_history.csv alongside the training sources. No DB needed — Jinja render only.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined

REPO = Path(__file__).resolve().parent.parent
CFG = REPO / "extraction/configs/extract_training_assist.yaml"
SQL = REPO / "extraction/sql/coaching_history.sql.j2"


def _context() -> dict:
    cfg = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    out = cfg["outputs"]["coaching_history"]
    return {**(cfg.get("globals") or {}), **(out.get("params") or {})}   # params override globals


def _render_coaching_history() -> str:
    env = Environment(undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    return env.from_string(SQL.read_text(encoding="utf-8")).render(**_context())


def test_training_config_has_coaching_history_output():
    cfg = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    out = cfg["outputs"]["coaching_history"]
    assert out["output_file"] == "coaching_history.csv"
    assert out["sql_file"].endswith("coaching_history.sql.j2")


def test_coaching_history_renders_in_training_context():
    # No StrictUndefined error => every var the template needs is defined in the training config.
    ctx = _context()
    sql = _render_coaching_history()
    assert "l2_asurion_coachdb_coachdb_helixcoaching" in sql
    # date window comes from the config (globals, or a per-output override if present)
    assert f"DATE '{ctx['start_date']}'" in sql and f"DATE '{ctx['end_date']}'" in sql
    # status IN-list rendered from the list param
    assert "'Submitted'" in sql and "'Excused'" in sql
    # unbounded: no agent/roster predicate is pushed into the SQL (cohort filtering is in Python)
    assert "coachee_emp_id IN" not in sql
