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
from pathlib import Path

import pandas as pd

from cde.cli import run_pipeline as base_pipeline
from cde.reporting.training_dashboard import build_training_records, write_training_dashboard
from cde.training.program import load_program, load_policy
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
    ap.add_argument("--report-date", default=None, help="YYYY-MM-DD; default = latest window end in the run")
    ap.add_argument("--pass-mark", type=float, default=0.80)
    args = ap.parse_args(argv)

    configs = Path(args.configs_dir)
    raw = Path(args.raw_dir)
    out = Path(args.out_dir) if args.out_dir else Path("outputs/training_runs") / args.run_id
    program_path = Path(args.program) if args.program else configs / "training_program.yaml"
    policy_path = Path(args.policy) if args.policy else configs / "remediation.yaml"
    profiles_path = configs.parent / "mappings" / "training_profiles.yaml"

    # 1) shared engine over the training config set
    base_pipeline.main(["--configs-dir", str(configs), "--raw-dir", str(raw),
                        "--out-dir", str(out), "--run-id", args.run_id])

    # 2) assemble dashboard inputs
    skills_df = _build_skills_df(out)
    agents_path = raw / "agents.csv"
    agents_df = pd.read_csv(agents_path) if agents_path.exists() else pd.DataFrame(
        {"agent_id": skills_df["agent_id"].astype(str).unique()})
    program = load_program(program_path)
    policy = load_policy(policy_path)
    skill_meta = _skill_meta(profiles_path)

    report_date = args.report_date
    if report_date is None:
        try:
            sw = pd.read_csv(out / "scores_windowed.csv")
            report_date = str(pd.to_datetime(sw["window_end"]).max().date())
        except Exception:  # noqa: BLE001
            report_date = "2026-09-01"

    records, meta = build_training_records(skills_df, agents_df, program, policy, skill_meta,
                                           report_date=report_date, pass_mark=args.pass_mark)
    path = write_training_dashboard(out, records, meta)
    n_rem = sum(1 for r in records if r["remediation"])
    print(f"training dashboard: {len(records)} experts, {n_rem} with remediation -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
