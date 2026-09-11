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
    _norm,
    ascend_challenge_ids,
    build_behavior_skill_map,
)
from pde.reporting.training_dashboard import build_training_records, write_training_dashboard
from pde.training.program import cbt_metric_key, load_program, load_policy
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


def _agg_taken(df: pd.DataFrame, key_cols: list, rate_agg: str = "mean") -> pd.DataFrame:
    """Roll day-grain rows up to per-(agent, key) practice aggregates:
    sessions = distinct practice days, pass_rate = <rate_agg>(calc), last_period = max(period).
    `rate_agg` is "mean" (default) or "max" (highest attempt -- used for CBT top line)."""
    df = df.copy()
    df["calc"] = pd.to_numeric(df.get("calc"), errors="coerce")
    g = df.groupby(["agent_id"] + key_cols, dropna=False)
    out = g.agg(sessions=("period", "nunique"),
                pass_rate=("calc", rate_agg),
                last_period=("period", "max")).reset_index()
    out["pass_rate"] = out["pass_rate"].round(3)
    return out


def _sim_attempts(raw_dir: Path) -> dict:
    """Per-(agent_id, challenge_id) list of individual sim attempts, newest-first.

    From the session-grain extract. Each attempt: {when, result, passed, present, max, ratio}.
    Ordered by session_start_time when present, else period + session_id (day-order fallback
    until the extract is re-run with session_start_time). Keyed (agent_id, challenge_id_lower)."""
    path = raw_dir / "training_assist_sessions.csv"
    if not path.exists():
        return {}
    df = pd.read_csv(path, dtype={"agent_id": str, "scorecard_name": str, "session_id": str,
                                  "evaluation_results": str, "period": str, "session_start_time": str})
    if df.empty:
        return {}
    order_col = "session_start_time" if "session_start_time" in df.columns else "period"
    df = df.sort_values([order_col, "session_id"], ascending=False)
    df = df.drop_duplicates("session_id", keep="first")   # one row per real session (drop CDC dupes)
    pres = pd.to_numeric(df.get("present_behaviors"), errors="coerce")
    mx = pd.to_numeric(df.get("max_behaviors"), errors="coerce")
    df["ratio_val"] = (pres / mx.where(mx > 0)).clip(0.0, 1.0)
    out: dict = {}
    for r in df.itertuples():
        key = (str(r.agent_id), str(r.scorecard_name).strip().lower())
        when = getattr(r, "session_start_time", None)
        if when is None or (isinstance(when, float) and pd.isna(when)):
            when = r.period
        passed = _norm_result(r.evaluation_results)
        p = getattr(r, "present_behaviors", None); m = getattr(r, "max_behaviors", None)
        ratio = getattr(r, "ratio_val", None)
        out.setdefault(key, []).append({
            "when": str(when),
            "result": (None if r.evaluation_results is None or (isinstance(r.evaluation_results, float) and pd.isna(r.evaluation_results)) else str(r.evaluation_results)),
            "passed": None if passed is None else bool(passed),
            "present": None if p is None or pd.isna(p) else int(p),
            "max": None if m is None or pd.isna(m) else int(m),
            "ratio": None if ratio is None or pd.isna(ratio) else round(float(ratio), 3),
        })
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
            "last_period", "passed", "result", "present_ratio", "attempts"]
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

    # Per-attempt drill-down list (newest-first), keyed (agent_id, challenge_id_lower).
    att = _sim_attempts(raw_dir)
    agg["attempts"] = [att.get((str(a), str(c).strip().lower()), []) for a, c in
                       zip(agg["agent_id"], agg["challenge_id"])]
    # Top-line session count = real attempts (so it matches the drill-down row count); fall back to
    # the day-based count only when there is no session-grain data for the sim.
    agg["sessions"] = [len(a) if a else s for a, s in zip(agg["attempts"], agg["sessions"])]
    return agg[cols]


def _build_cbts_taken(raw_dir: Path, block_by_ref: dict) -> pd.DataFrame:
    """Per-(agent, course) CBT summary, joined to blocks via the program's `kind: cbt` components.

    Each course is classified so the dashboard can show it accurately instead of a score it will
    never receive:
      - `scored`   : the course is a scored assessment (some calc>0 anywhere in the extract);
                     `pass_rate` is then its HIGHEST attempt score (max calc).
      - `completed`: the expert has completion records for it (>=1 session).
    Completion-only courses (never scored) get `pass_rate = NA` -> the UI renders 'Completed'.
    `block_num` is null for courses not represented by a cbt component (still shown, just unmapped).
    `attempts` is the per-completion-day drill-down list (newest-first).

    Columns: agent_id, courseid, coursename, block_num, scored, completed, sessions, pass_rate,
    last_period, attempts."""
    cols = ["agent_id", "courseid", "coursename", "ref", "block_num", "scored", "completed",
            "sessions", "pass_rate", "last_period", "attempts"]
    path = raw_dir / "training_cbt.csv"
    if not path.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(path, dtype={"agent_id": str, "courseid": str, "coursename": str, "period": str})
    df["calc"] = pd.to_numeric(df.get("calc"), errors="coerce")
    scored_ids = set(df.groupby("courseid")["calc"].max().pipe(lambda s: s[s > 0]).index)
    agg = _agg_taken(df, ["courseid", "coursename"], rate_agg="max")     # top line = HIGHEST attempt
    agg["ref"] = agg["courseid"].map(cbt_metric_key)                     # matches the cbt component ref
    agg["block_num"] = agg["ref"].map(block_by_ref)
    agg["scored"] = agg["courseid"].isin(scored_ids)
    agg["completed"] = agg["sessions"] > 0
    agg.loc[~agg["scored"], "pass_rate"] = pd.NA   # completion-only: no score to report

    # Per-attempt drill-down (newest-first), keyed (agent_id, courseid).
    att: dict = {}
    for r in df.sort_values("period", ascending=False).itertuples():
        key = (str(r.agent_id), str(r.courseid))
        att.setdefault(key, []).append({
            "when": str(r.period),
            "score": None if pd.isna(r.calc) else round(float(r.calc), 3),
        })
    agg["attempts"] = [att.get((str(a), str(c)), []) for a, c in zip(agg["agent_id"], agg["courseid"])]
    # Top-line session count = number of attempt rows shown in the drill-down (per completion-day).
    agg["sessions"] = [len(a) if a else s for a, s in zip(agg["attempts"], agg["sessions"])]
    return agg[cols]


def _class_training_days(raw_dir: Path, roster_df: pd.DataFrame) -> dict:
    """{class_id: sorted list of distinct dates the CLASS had training activity}.

    A "class training day" = any date on which any roster member of that class has a row in
    training_assist.csv OR training_cbt.csv. Used to measure pacing in TRAINING days rather than
    calendar days, so weekends / holidays / class-wide off days (no data) don't count while an
    individual's absence still does (the class had data that day). NOT ASCEND-filtered on purpose --
    this is "did the class train", so maximize coverage across all logged training activity.
    """
    if roster_df is None or roster_df.empty:
        return {}
    amap = dict(zip(roster_df["agent_id"].astype(str), roster_df["class_id"].astype(str)))
    frames = []
    for name in ("training_assist.csv", "training_cbt.csv"):
        p = raw_dir / name
        if not p.exists():
            continue
        df = pd.read_csv(p, dtype={"agent_id": str, "period": str}, usecols=lambda c: c in ("agent_id", "period"))
        if df.empty or "agent_id" not in df.columns or "period" not in df.columns:
            continue
        df["class_id"] = df["agent_id"].map(amap)
        df = df.dropna(subset=["class_id", "period"])
        frames.append(df[["class_id", "period"]])
    if not frames:
        return {}
    allp = pd.concat(frames, ignore_index=True).drop_duplicates()
    return {str(cid): sorted(g["period"].astype(str).unique())
            for cid, g in allp.groupby("class_id")}


def _build_block_skills(raw_dir: Path, program, profiles: dict, pass_mark: float = 0.80) -> pd.DataFrame:
    """Per-(agent, block, skill) LATEST-attempt pass-rate, tagged CORE vs SURFACED.

    Reads the session-grain behavior extract (training_assist_behaviors.csv). For each (agent, sim)
    it keeps only the LATEST session (max session_start_time), maps behavior->skill, crosswalks
    challenge_id -> sim_id -> block, and aggregates per (agent, block, skill).

    Every skill a block's sims actually exercised is emitted (requirements no longer gate the block
    DISPLAY). Each row carries `core`:
      - core = True  : the skill is in that block's `develops` -- a core learning-block skill (scored,
                       and the only skills that gate closing out / retraining the block downstream).
      - core = False : "surfaced-but-not-trained" -- exercised by this block's sims but trained in
                       another block; shown for visibility, excluded from closing out / remediation.

    Columns: agent_id, block_num, skill, value, below, core.
    """
    cols = ["agent_id", "block_num", "skill", "value", "below", "core"]
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

    # --- behavior -> skill (ALL observed skills the sim surfaced; core vs surfaced is decided by the
    #     block's develops below, not by persona requirements) ---
    df["skill"] = df["behavior"].map(_norm).map(b2s)
    df = df[df["skill"].notna()].copy()
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

    # --- core vs surfaced: CORE iff the skill is in that block's develops (what the block trains) ---
    develops_by_order = {b.order: set(b.develops_all) for b in program.blocks}
    grp["core"] = [sk in develops_by_order.get(bn, set()) for sk, bn in zip(grp["skill"], grp["block_num"])]

    grp["below"] = grp["value"] < pass_mark
    return grp[cols].sort_values(["agent_id", "block_num", "core", "skill"],
                                 ascending=[True, True, False, True]).reset_index(drop=True)


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
    ap.add_argument("--publish-dir", default=None,
                    help="Extra directory to ALSO write (overwrite) the training dashboard into, on top "
                         "of --out-dir -- a stable copy for a downstream push to a remote folder. "
                         "Overrides training_dashboard.publish_dir in active.yaml.")
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

    # Pace by TRAINING days (distinct days the class actually had activity), not calendar days.
    class_training_days = _class_training_days(raw, agents_df)

    records, meta = build_training_records(skills_df, agents_df, program, policy, skill_meta,
                                           report_date=report_date, pass_mark=args.pass_mark,
                                           coaching_history=coaching_history,
                                           sims_taken=sims_taken, cbts_taken=cbts_taken,
                                           block_skills=block_skills,
                                           class_training_days=class_training_days)
    path = write_training_dashboard(out, records, meta)
    n_rem = sum(1 for r in records if r["remediation"])
    print(f"training dashboard: {len(records)} experts, {n_rem} with remediation -> {path}")

    # Optional stable publish copy: --publish-dir, else active.yaml training_dashboard.publish_dir.
    # Always overwrites training_dashboard.{html,json} there (a downstream job pushes it to a remote folder).
    publish_dir = args.publish_dir
    if not publish_dir:
        active_cfg = load_yaml(configs / "active.yaml") or {}
        publish_dir = str((active_cfg.get("training_dashboard") or {}).get("publish_dir") or "").strip()
    if publish_dir:
        pub_path = write_training_dashboard(Path(publish_dir), records, meta)
        log.info("training dashboard also published (overwritten) to %s", pub_path)
        print(f"  .. also published to {pub_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
