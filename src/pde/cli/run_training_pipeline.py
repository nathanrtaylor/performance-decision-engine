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
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

from pde.cli import run_pipeline as base_pipeline
from pde.ingestion.training_assist_skills import (
    _counted_levels,
    _norm,
    ascend_challenge_ids,
    build_behavior_skill_map,
    build_profile_requirements,
)
from pde.reporting.training_dashboard import build_training_records, write_training_dashboard
from pde.training.program import build_skill_routing, cbt_metric_key, load_program, load_policy
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


def _norm_result(v) -> Optional[bool]:
    """The simulator's `Results` string -> pass (True) / fail (False) / unknown (None).

    The observed value set is "Cleared" / "Not cleared"; also handles Pass(ed) / Fail(ed).
    NOTE: negatives are matched FIRST -- "Not cleared" contains "cleared", so a naive
    pass-check would misread every fail as a pass. Anything unrecognized is unknown (None);
    callers log unmapped values so a new verdict string surfaces rather than silently passing.
    """
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip().lower()
    if not s:
        return None
    if s.startswith("not ") or s.startswith("fail") or s.startswith("uncleared"):
        return False
    if s.startswith("clear") or s.startswith("pass"):
        return True
    return None


def _build_sim_results(raw_dir: Path) -> pd.DataFrame:
    """Per-(agent, sim) NATIVE pass/fail verdict + present/total evidence.

    Reads the session-grain extract (training_assist_sessions.csv: one row per sim attempt).
    Verdict is best/any-pass -- a sim passes if ANY attempt's `Results` says pass.
    `present_ratio` is the best attempt's behaviors_present / max_behaviors (headline evidence).

    Columns: agent_id, challenge_id, passed, result, present_ratio.
    """
    cols = ["agent_id", "challenge_id", "passed", "result", "present_ratio"]
    path = raw_dir / "training_assist_sessions.csv"
    if not path.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(path, dtype={"agent_id": str, "scorecard_name": str, "session_id": str,
                                  "evaluation_results": str, "period": str})
    if df.empty:
        return pd.DataFrame(columns=cols)
    df = df.rename(columns={"scorecard_name": "challenge_id"})
    df["_passed"] = df["evaluation_results"].map(_norm_result)

    # data-quality visibility: distinct Results values we could not map to pass/fail
    unmapped = sorted({str(v).strip() for v, p in zip(df["evaluation_results"], df["_passed"])
                       if p is None and v is not None and str(v).strip() != ""})
    if unmapped:
        log.warning("training_assist_sessions: %d unmapped evaluation_results value(s) "
                    "(treated as unknown -- confirm the Pass/Fail enum): %s", len(unmapped), unmapped[:25])

    pres = pd.to_numeric(df.get("present_behaviors"), errors="coerce")
    mx = pd.to_numeric(df.get("max_behaviors"), errors="coerce")
    df["_ratio"] = (pres / mx.where(mx > 0)).clip(0.0, 1.0)

    def _roll(g: pd.DataFrame) -> pd.Series:
        vals = [p for p in g["_passed"] if p is not None]
        passed = (True if any(vals) else False) if vals else None   # any-pass; None if no verdict
        ratio = g["_ratio"].max(skipna=True)
        return pd.Series({
            "passed": passed,
            "result": None if passed is None else ("Pass" if passed else "Fail"),
            "present_ratio": None if pd.isna(ratio) else round(float(ratio), 3),
        })

    out = (df.groupby(["agent_id", "challenge_id"], dropna=False)
           .apply(_roll).reset_index())
    return out[cols]


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

    Columns: agent_id, challenge_id, label, sim_id, block_num, sessions, pass_rate, last_period,
    passed, result, present_ratio.
    `sim_id`/`block_num` are populated only where the challenge_id resolves through
    training_profiles.yaml (sim_id) and a training_program.yaml component (ref -> block); else null.
    `passed`/`result`/`present_ratio` come from the session-grain extract (the native verdict +
    present/total evidence); null where no session-grain data exists for the sim.
    """
    cols = ["agent_id", "challenge_id", "label", "sim_id", "block_num", "sessions", "pass_rate",
            "last_period", "passed", "result", "present_ratio"]
    path = raw_dir / "training_assist.csv"
    if not path.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(path, dtype={"agent_id": str, "scorecard_name": str, "period": str})
    agg = _agg_taken(df.rename(columns={"scorecard_name": "challenge_id"}), ["challenge_id"])

    profiles = load_yaml(profiles_path) or {}

    # ASCEND scope: non-ASCEND personas (lob != ASCEND Launchpad) never appear in the dashboard
    # -- not under a block and not in "other sims practiced".
    ascend = ascend_challenge_ids(profiles)
    agg = agg[agg["challenge_id"].map(lambda c: str(c).strip().lower() in ascend)].copy()

    prof = profiles.get("profiles") or {}
    cid_sim = {str(c): (p or {}).get("sim_id") for c, p in prof.items()}
    cid_label = {str(c): ((p or {}).get("reporting_label") or (p or {}).get("source_profile_name") or str(c))
                 for c, p in prof.items()}
    ref_block = {c.ref: b.order for b in program.blocks for c in b.components if c.ref}

    agg["sim_id"] = agg["challenge_id"].map(cid_sim)
    agg["label"] = agg["challenge_id"].map(lambda c: cid_label.get(c, c))
    agg["block_num"] = agg["sim_id"].map(ref_block)

    # Native Pass/Fail verdict + present/total evidence (session-grain extract). Left-join so
    # sims with no session-grain row (e.g. old data) simply carry nulls and fall back to pass_rate.
    verdict = _build_sim_results(raw_dir)
    agg = agg.merge(verdict, on=["agent_id", "challenge_id"], how="left")
    for c in ("passed", "result", "present_ratio"):
        if c not in agg.columns:
            agg[c] = pd.NA
    return agg[cols]


def _build_cbts_taken(raw_dir: Path, block_by_ref: dict) -> pd.DataFrame:
    """Per-(agent, course) CBT summary, joined to blocks via the program's `kind: cbt` components.

    Each course is classified so the dashboard can show it accurately instead of a score it will
    never receive:
      - `scored`   : the course is a scored assessment (some calc>0 anywhere in the extract);
                     `pass_rate` is then its mean score.
      - `completed`: the expert has completion records for it (>=1 session).
    Completion-only courses (never scored) get `pass_rate = NA` -> the UI renders 'Completed'.
    `block_num` is null for courses not represented by a cbt component (still shown, just unmapped).

    Columns: agent_id, courseid, coursename, block_num, scored, completed, sessions, pass_rate, last_period."""
    cols = ["agent_id", "courseid", "coursename", "ref", "block_num", "scored", "completed",
            "sessions", "pass_rate", "last_period"]
    path = raw_dir / "training_cbt.csv"
    if not path.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(path, dtype={"agent_id": str, "courseid": str, "coursename": str, "period": str})
    df["calc"] = pd.to_numeric(df.get("calc"), errors="coerce")
    scored_ids = set(df.groupby("courseid")["calc"].max().pipe(lambda s: s[s > 0]).index)
    agg = _agg_taken(df, ["courseid", "coursename"])
    agg["ref"] = agg["courseid"].map(cbt_metric_key)                     # matches the cbt component ref
    agg["block_num"] = agg["ref"].map(block_by_ref)
    agg["scored"] = agg["courseid"].isin(scored_ids)
    agg["completed"] = agg["sessions"] > 0
    agg.loc[~agg["scored"], "pass_rate"] = pd.NA   # completion-only: no score to report
    return agg[cols]


def _build_block_skills(raw_dir: Path, program, profiles: dict, pass_mark: float = 0.80) -> pd.DataFrame:
    """Per-(agent, block, skill) LATEST-attempt pass-rate -- block-discrete skill scoring.

    Reads the session-grain behavior extract (training_assist_behaviors.csv). For each
    (agent, sim) it keeps only the LATEST session (max session_start_time), maps behavior->skill
    under the profile relevance rules, crosswalks challenge_id -> sim_id -> block, aggregates per
    (agent, block, skill), and keeps a (block, skill) only where the skill is taught at/before that
    block (the asymmetry: earlier-taught skills may surface on later blocks, never the reverse).

    Columns: agent_id, block_num, skill, value, below.
    """
    cols = ["agent_id", "block_num", "skill", "value", "below"]
    path = raw_dir / "training_assist_behaviors.csv"
    if not path.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(path, dtype={"agent_id": str, "session_id": str, "scorecard_name": str,
                                  "session_start_time": str, "behavior": str})
    if df.empty:
        return pd.DataFrame(columns=cols)

    prof = profiles.get("profiles") or {}
    ascend = ascend_challenge_ids(profiles)
    b2s = build_behavior_skill_map(profiles)
    prof_req = build_profile_requirements(profiles, _counted_levels(profiles))

    df["_cid"] = df["scorecard_name"].map(_norm)
    df = df[df["_cid"].isin(ascend)].copy()               # ASCEND scope
    if df.empty:
        return pd.DataFrame(columns=cols)

    # --- latest attempt: keep only rows of each (agent, sim)'s most recent session ---
    df = df.sort_values(["session_start_time", "session_id"])
    latest = (df.groupby(["agent_id", "_cid"], dropna=False)["session_id"].last()
              .rename("_latest_sid").reset_index())
    df = df.merge(latest, on=["agent_id", "_cid"])
    df = df[df["session_id"] == df["_latest_sid"]].copy()

    # --- behavior -> skill, keep only skills that are a counted requirement for the sim ---
    df["skill"] = df["behavior"].map(_norm).map(b2s)
    df = df[df["skill"].notna()].copy()
    rel = [sk in prof_req.get(c, ()) for sk, c in zip(df["skill"], df["_cid"])]
    df = df[pd.Series(rel, index=df.index)].copy()
    if df.empty:
        return pd.DataFrame(columns=cols)

    # --- crosswalk challenge_id -> sim_id -> block; drop sims that don't resolve to a block ---
    cid_sim = {_norm(p.get("challenge_id") or pid): (p or {}).get("sim_id")
               for pid, p in ((k, v or {}) for k, v in prof.items())}
    ref_block = {c.ref: b.order for b in program.blocks for c in b.components if c.ref}
    df["block_num"] = df["_cid"].map(cid_sim).map(ref_block)
    df = df[df["block_num"].notna()].copy()
    if df.empty:
        return pd.DataFrame(columns=cols)

    # --- aggregate to (agent, block, skill): value = present / evaluated across latest sessions ---
    df["behavior_present"] = pd.to_numeric(df["behavior_present"], errors="coerce").fillna(0.0)
    grp = (df.groupby(["agent_id", "block_num", "skill"], dropna=False)
           .agg(numerator=("behavior_present", "sum"), denominator=("behavior_present", "size"))
           .reset_index())
    grp["block_num"] = grp["block_num"].astype(int)
    grp["value"] = grp["numerator"] / grp["denominator"].where(grp["denominator"] != 0)

    # --- asymmetry filter: surface a skill under block N only if it is taught at/before N ---
    routing = build_skill_routing(program)
    order_by_id = {b.id: b.order for b in program.blocks}

    def _taught_at_or_before(skill: str, block_num: int) -> bool:
        blocks = routing.get(skill) or []
        if not blocks:                                    # skill not in any curriculum map -> keep
            return True
        return min(order_by_id.get(bid, 10 ** 9) for bid in blocks) <= block_num

    keep = [_taught_at_or_before(sk, bn) for sk, bn in zip(grp["skill"], grp["block_num"])]
    grp = grp[pd.Series(keep, index=grp.index)].copy()

    grp["below"] = grp["value"] < pass_mark
    return grp[cols].sort_values(["agent_id", "block_num", "skill"]).reset_index(drop=True)


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
    # CBTs nest under blocks via the program's `kind: cbt` components (ref = cbt_<courseid> metric key),
    # the source of truth maintained by tools/gen_program_components.py.
    sims_taken = _build_sims_taken(raw, program, profiles_path)
    cbt_block_by_ref = {c.ref: b.order for b in program.blocks for c in b.components
                        if getattr(c, "kind", None) == "cbt" and c.ref}
    cbts_taken = _build_cbts_taken(raw, cbt_block_by_ref)

    # Block-discrete, latest-attempt skill scores (the dashboard's per-block skill source).
    # Until the session-grain behavior extract exists, fall back to the legacy develops-map path
    # (pass None) rather than blanking every block's skills.
    block_skills = None
    if (raw / "training_assist_behaviors.csv").exists():
        block_skills = _build_block_skills(raw, program, load_yaml(profiles_path) or {}, args.pass_mark)

    records, meta = build_training_records(skills_df, agents_df, program, policy, skill_meta,
                                           report_date=report_date, pass_mark=args.pass_mark,
                                           coaching_history=coaching_history,
                                           sims_taken=sims_taken, cbts_taken=cbts_taken,
                                           block_skills=block_skills)
    path = write_training_dashboard(out, records, meta)
    n_rem = sum(1 for r in records if r["remediation"])
    print(f"training dashboard: {len(records)} experts, {n_rem} with remediation -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
