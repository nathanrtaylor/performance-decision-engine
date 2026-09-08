"""CLI: run the training decision pipeline end to end (Phase 4).

Runs the shared engine over the training config set (producing signals / scores /
receipts via the standard pipeline), then builds the training dashboard: per-expert
remediation plans (Learning / Training-support / Coaching actions), learning-block +
skill rollups, and pacing (on track vs each block's expected completion day).

    python -m pde.cli.run_training_pipeline --raw-dir data/raw/adhoc/latest \
        --out-dir outputs/training_runs/<date>

Outputs (in --out-dir): the usual pipeline artifacts plus training_dashboard.{html,json}.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from pde.cli import run_pipeline as base_pipeline
from pde.reporting.training_dashboard import build_training_records, write_training_dashboard
from pde.training.program import load_program, load_policy
from pde.training.roster import load_class_roster
from pde.utils.io import load_yaml
from pde.utils.logging import get_logger

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


def _agg_taken(df: pd.DataFrame, key_cols: list) -> pd.DataFrame:
    """Roll day-grain rows up to per-(agent, key) practice aggregates:
    sessions = distinct practice days, pass_rate = mean(calc), last_period = max(period)."""
    df = df.copy()
    df["calc"] = pd.to_numeric(df.get("calc"), errors="coerce")
    g = df.groupby(["agent_id"] + key_cols, dropna=False)
    out = g.agg(sessions=("period", "nunique"),
                pass_rate=("calc", "mean"),
                last_period=("period", "max")).reset_index()
    out["pass_rate"] = out["pass_rate"].round(3)
    return out


def _build_sims_taken(raw_dir: Path, program, profiles_path: Path) -> pd.DataFrame:
    """Per-(agent, sim) TrAIning Assist practice summary, enriched with the profile crosswalk.

    Columns: agent_id, challenge_id, label, sim_id, block_num, sessions, pass_rate, last_period.
    `sim_id`/`block_num` are populated only where the challenge_id resolves through
    training_profiles.yaml (sim_id) and a training_program.yaml component (ref -> block); else null.
    """
    cols = ["agent_id", "challenge_id", "label", "sim_id", "block_num", "sessions", "pass_rate", "last_period"]
    path = raw_dir / "training_assist.csv"
    if not path.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(path, dtype={"agent_id": str, "scorecard_name": str, "period": str})
    agg = _agg_taken(df.rename(columns={"scorecard_name": "challenge_id"}), ["challenge_id"])

    prof = (load_yaml(profiles_path) or {}).get("profiles") or {}
    cid_sim = {str(c): (p or {}).get("sim_id") for c, p in prof.items()}
    cid_label = {str(c): ((p or {}).get("reporting_label") or (p or {}).get("source_profile_name") or str(c))
                 for c, p in prof.items()}
    ref_block = {c.ref: b.order for b in program.blocks for c in b.components if c.ref}

    agg["sim_id"] = agg["challenge_id"].map(cid_sim)
    agg["label"] = agg["challenge_id"].map(lambda c: cid_label.get(c, c))
    agg["block_num"] = agg["sim_id"].map(ref_block)
    return agg[cols]


def _norm_course(s) -> str:
    """Normalize a course/CBT-link name for joining (the xlsx CBT-link names end ' - Workday')."""
    s = re.sub(r"\s*-\s*workday\s*$", "", str(s).strip().lower())
    return re.sub(r"[^0-9a-z]+", " ", s).strip()


def _cbt_block_map(sims_xlsx: Path) -> dict:
    """normalized coursename -> learning-block order, from the xlsx 'WDL CBT Link' + 'Learning Block'.
    This is the only content source tying CBTs to blocks; empty {} if the workbook is absent."""
    if not sims_xlsx.exists():
        return {}
    try:
        df = pd.read_excel(sims_xlsx, sheet_name="Sims 100")
    except Exception:  # noqa: BLE001 -- a missing/renamed sheet just means no CBT->block map
        return {}
    out: dict = {}
    for _, r in df.iterrows():
        link, blk = r.get("WDL CBT Link"), r.get("Learning Block")
        if pd.isna(link) or pd.isna(blk):
            continue
        out.setdefault(_norm_course(link), int(blk))
    return out


def _build_cbts_taken(raw_dir: Path, block_map: dict) -> pd.DataFrame:
    """Per-(agent, course) CBT completion summary, with block_num joined from the xlsx CBT-link map.

    Columns: agent_id, courseid, coursename, block_num, sessions, pass_rate, last_period.
    `block_num` is null for courses not present in the xlsx 'WDL CBT Link' column (most of them)."""
    cols = ["agent_id", "courseid", "coursename", "block_num", "sessions", "pass_rate", "last_period"]
    path = raw_dir / "training_cbt.csv"
    if not path.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(path, dtype={"agent_id": str, "courseid": str, "coursename": str, "period": str})
    agg = _agg_taken(df, ["courseid", "coursename"])
    agg["block_num"] = agg["coursename"].map(lambda c: block_map.get(_norm_course(c)))
    return agg[cols]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the training decision pipeline + dashboard.")
    ap.add_argument("--configs-dir", default="configs/training")
    ap.add_argument("--raw-dir", default="data/raw/adhoc/latest")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--run-id", default="training_run")
    ap.add_argument("--program", default=None, help="training_program.yaml (default: <configs-dir>/training_program.yaml)")
    ap.add_argument("--policy", default=None, help="remediation.yaml (default: <configs-dir>/remediation.yaml)")
    ap.add_argument("--roster", default="data/training/training_class_roster.xlsx",
                    help="Class roster xlsx — the source of truth for who/class/trainer/start date. "
                         "Lives under data/training/ (gitignored: PII, updated per run).")
    ap.add_argument("--report-date", default=None, help="YYYY-MM-DD; default = today (start of the timeline count is each expert's start date)")
    ap.add_argument("--pass-mark", type=float, default=0.80)
    ap.add_argument("--sims-xlsx", default="docs/training/Ascend Simulations.xlsx",
                    help="Ascend Simulations workbook; its 'WDL CBT Link' + Learning Block columns map "
                         "CBTs to learning blocks (nested under blocks in the dashboard).")
    ap.add_argument("--coaching-history", default=None,
                    help="coaching_history.csv for the 'Recent coaching history' block. Default: "
                         "<raw-dir>/coaching_history.csv, else data/raw/weekly/latest/coaching_history.csv.")
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

    # Recent coaching history (optional): first existing of --coaching-history,
    # <raw-dir>/coaching_history.csv (produced by the training extract), then the weekly latest.
    ch_candidates = [args.coaching_history, raw / "coaching_history.csv",
                     Path("data/raw/weekly/latest/coaching_history.csv")]
    coaching_history = None
    for cand in ch_candidates:
        if cand and Path(cand).exists():
            coaching_history = pd.read_csv(cand)
            log.info("coaching history: %d rows from %s", len(coaching_history), cand)
            break

    # Sims practiced + CBTs completed (per expert), from the raw extract in --raw-dir.
    # CBTs are mapped to blocks via the xlsx 'WDL CBT Link' column (the only course->block source).
    sims_taken = _build_sims_taken(raw, program, profiles_path)
    cbts_taken = _build_cbts_taken(raw, _cbt_block_map(Path(args.sims_xlsx)))

    records, meta = build_training_records(skills_df, agents_df, program, policy, skill_meta,
                                           report_date=report_date, pass_mark=args.pass_mark,
                                           coaching_history=coaching_history,
                                           sims_taken=sims_taken, cbts_taken=cbts_taken)
    path = write_training_dashboard(out, records, meta)
    n_rem = sum(1 for r in records if r["remediation"])
    print(f"training dashboard: {len(records)} experts, {n_rem} with remediation -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
