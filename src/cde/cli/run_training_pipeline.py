"""CLI: run the training decision pipeline end to end (Phase 4).

Runs the shared engine over the training config set (producing signals / scores /
receipts via the standard pipeline), then builds the training dashboard: per-expert
remediation plans (Learning / Training-support / Coaching actions), learning-block +
skill rollups, and pacing (on track vs each block's expected completion day).

    python -m cde.cli.run_training_pipeline --raw-dir data/raw/adhoc/latest \
        --out-dir outputs/training_runs/<date>

Outputs (in --out-dir): the usual pipeline artifacts plus training_dashboard.{html,json}.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from cde.cli import run_pipeline as base_pipeline
from cde.reporting.training_dashboard import build_training_records, write_training_dashboard
from cde.training.program import load_program, load_policy
from cde.training.roster import load_class_roster
from cde.utils.io import load_yaml
from cde.utils.logging import get_logger

log = get_logger(__name__)


def _skill_meta(profiles_path: Path) -> dict:
    prof = load_yaml(profiles_path) or {}
    cats = prof.get("skill_categories") or {}
    out = {}
    for sid, meta in (prof.get("skills") or {}).items():
        cat = (meta or {}).get("category")
        out[str(sid)] = {"label": (meta or {}).get("label") or sid,
                         "category": (cats.get(cat) or {}).get("label", cat or "")}
    return out


def _ensure_engine_agents(raw: Path) -> None:
    """Regenerate the engine's `agents` dimension from the current training data.

    The base pipeline needs an agents.csv (icp_client enrichment + expected_sources).
    We derive it from the day-grain training sources so it always matches the experts
    in the run — the class roster is the DASHBOARD's source of truth; this is only the
    engine's cohort label. Overwrites any stale agents.csv.
    """
    frames = []
    for name in ("training_assist_skills.csv", "training_cbt.csv"):
        p = raw / name
        if not p.exists():
            continue
        df = pd.read_csv(p)
        if "period" not in df.columns and "week_ending" in df.columns:
            df = df.rename(columns={"week_ending": "period"})
        if {"agent_id", "period"} <= set(df.columns):
            frames.append(df[["agent_id", "period"]])
    if not frames:
        return
    a = pd.concat(frames, ignore_index=True).drop_duplicates()
    a["week_ending"] = a["period"]                 # build_signals enrichment reads week_ending
    a["icp_client"] = "training"
    for c in ("mascot", "coach", "coach_id"):
        a[c] = ""
    a[["agent_id", "week_ending", "icp_client", "mascot", "coach", "coach_id"]].to_csv(
        raw / "agents.csv", index=False)
    log.info("engine agents dimension: wrote %d rows to %s", len(a), raw / "agents.csv")


def _build_skills_df(out_dir: Path) -> pd.DataFrame:
    """Per-(agent, metric) pass-rate value + benchmark for the dashboard's skill rollup."""
    es = out_dir / "eligible_signals.csv"
    if es.exists():
        df = pd.read_csv(es)
        if {"agent_id", "metric", "value", "benchmark"} <= set(df.columns):
            return (df.groupby(["agent_id", "metric"], dropna=False)
                    .agg(value=("value", "mean"), benchmark=("benchmark", "first"))
                    .reset_index())
    # fallback: reconstruct value from the windowed deficit
    sw = pd.read_csv(out_dir / "scores_windowed.csv")
    sw["value"] = sw["benchmark_8w"] - sw["level_8w"]
    return sw.rename(columns={"benchmark_8w": "benchmark"})[["agent_id", "metric", "value", "benchmark"]]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the training decision pipeline + dashboard.")
    ap.add_argument("--configs-dir", default="configs/training")
    ap.add_argument("--raw-dir", default="data/raw/adhoc/latest")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--run-id", default="training_run")
    ap.add_argument("--program", default=None, help="training_program.yaml (default: <configs-dir>/training_program.yaml)")
    ap.add_argument("--policy", default=None, help="remediation.yaml (default: <configs-dir>/remediation.yaml)")
    ap.add_argument("--roster", default="docs/training/training_class_roster.xlsx",
                    help="Class roster xlsx — the source of truth for who/class/trainer/start date.")
    ap.add_argument("--report-date", default=None, help="YYYY-MM-DD; default = today (start of the timeline count is each expert's start date)")
    ap.add_argument("--pass-mark", type=float, default=0.80)
    args = ap.parse_args(argv)

    configs = Path(args.configs_dir)
    raw = Path(args.raw_dir)
    out = Path(args.out_dir) if args.out_dir else Path("outputs/training_runs") / args.run_id
    program_path = Path(args.program) if args.program else configs / "training_program.yaml"
    policy_path = Path(args.policy) if args.policy else configs / "remediation.yaml"
    profiles_path = configs / "training_profiles.yaml"

    # 1) shared engine over the training config set (regenerate the engine agents
    #    dimension from the current data first, so it always matches this run's experts)
    _ensure_engine_agents(raw)
    base_pipeline.main(["--configs-dir", str(configs), "--raw-dir", str(raw),
                        "--out-dir", str(out), "--run-id", args.run_id])

    # 2) assemble dashboard inputs. The ROSTER is the source of truth for who
    #    appears + their class/trainer/start date; skills are left-joined by agent_id.
    skills_df = _build_skills_df(out)
    agents_df = load_class_roster(args.roster)
    program = load_program(program_path)
    policy = load_policy(policy_path)
    skill_meta = _skill_meta(profiles_path)

    # Timeline counts from each expert's start date to the report date (default: today).
    report_date = args.report_date or date.today().isoformat()

    records, meta = build_training_records(skills_df, agents_df, program, policy, skill_meta,
                                           report_date=report_date, pass_mark=args.pass_mark)
    path = write_training_dashboard(out, records, meta)
    n_rem = sum(1 for r in records if r["remediation"])
    print(f"training dashboard: {len(records)} experts, {n_rem} with remediation -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
