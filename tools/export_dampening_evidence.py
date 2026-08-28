"""Export per-agent dampening evidence for a saved run.

Dampening (src/cde/prioritization/dampening.py) halves the priority_score of a candidate
topic that the agent was coached on within the last `dampening.periods` weeks. The decision
receipts do NOT record this, so this tool reconstructs the evidence chain:

    dampened (agent, topic) candidate  ->  the coaching_history record(s) that triggered it

by joining the run's topic_candidates.csv (dampened flag) to the raw coaching_history table
the run consumed, mapped through configs/mappings/coaching_history_map.yaml (behavior_selected
-> topic), exactly as build_coaching_history does.

Writes <run_dir>/dampening_evidence.csv, one row per dampened (agent_id, period, call_type,
topic), with the candidate evidence, the pre/post-dampening priority, and the triggering
coaching behaviors + dates.

Usage:
    python tools/export_dampening_evidence.py outputs/runs/2026-08-28
    python tools/export_dampening_evidence.py outputs/runs/2026-08-28 --raw-dir data/raw/weekly/latest
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from cde.governance.versioning import resolve_active_config
from cde.utils.config import unwrap_root
from cde.utils.ids import normalize_agent_id

CONFIGS = Path(__file__).resolve().parents[1] / "configs"


def _raw_dir_from_manifest(run_dir: Path) -> Path | None:
    mf = run_dir / "manifest.json"
    if not mf.exists():
        return None
    try:
        raw = json.loads(mf.read_text(encoding="utf-8")).get("inputs", {}).get("raw_dir")
    except Exception:
        return None
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_absolute() else (Path.cwd() / p)


def _mapped_history(raw: pd.DataFrame, xmap: dict) -> pd.DataFrame:
    """Behavior-level coaching rows mapped to topics (keeps behavior + dates for evidence).

    ``coach_period`` mirrors build_coaching_history's date driver — the weekly ``period``
    bucket when present, else ``coaching_date`` — so the window test here reproduces exactly
    which candidates the pipeline dampened. ``coaching_date`` (the actual day) is retained
    only for human-readable evidence.
    """
    map_key = xmap.get("map_key", "behavior_selected")
    count_status = set(xmap.get("count_status") or [])
    count_types = {str(t).strip().casefold() for t in (xmap.get("count_types") or [])}
    b2t = {str(k).strip().casefold(): v for k, v in (xmap.get("behavior_to_topic") or {}).items()}

    h = raw.copy()
    if count_status and "coaching_status" in h.columns:
        h = h[h["coaching_status"].astype(str).str.strip().isin(count_status)]
    if count_types and "coaching_type" in h.columns:
        h = h[h["coaching_type"].astype(str).str.strip().str.casefold().isin(count_types)]
    h["topic"] = h[map_key].astype(str).str.strip().str.casefold().map(b2t)
    h = h[h["topic"].notna()].copy()
    h["_aid"] = normalize_agent_id(h["agent_id"])

    driver_col = "period" if "period" in h.columns else "coaching_date"
    h["coach_period"] = pd.to_datetime(h[driver_col], errors="coerce")
    h["coaching_date"] = pd.to_datetime(
        h["coaching_date"] if "coaching_date" in h.columns else h[driver_col], errors="coerce"
    )
    h = h[h["coach_period"].notna()]
    return h[["_aid", "topic", "coach_period", "coaching_date", map_key, "coaching_status"]].rename(
        columns={map_key: "behavior_selected"}
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--raw-dir", type=Path, default=None,
                    help="Override coaching_history source dir (default: from run manifest).")
    ap.add_argument("--configs", type=Path, default=CONFIGS)
    args = ap.parse_args()

    run_dir: Path = args.run_dir
    cfg = resolve_active_config(args.configs)

    damp = cfg.get("dampening") or {}
    mode = str(damp.get("mode", "multiply")).lower()
    periods = int(damp.get("periods", 2))
    multiplier = float(damp.get("multiplier", 0.5))

    cand = pd.read_csv(run_dir / "topic_candidates.csv")
    if "dampened" not in cand.columns:
        raise SystemExit("topic_candidates.csv has no `dampened` column — nothing to export.")
    d = cand[cand["dampened"] == True].copy()  # noqa: E712
    d["_aid"] = normalize_agent_id(d["agent_id"])
    d["period"] = pd.to_datetime(d["period"], errors="coerce")

    raw_dir = args.raw_dir or _raw_dir_from_manifest(run_dir)
    ch_path = (raw_dir / "coaching_history.csv") if raw_dir else None
    if not ch_path or not ch_path.exists():
        raise SystemExit(f"coaching_history.csv not found (looked in {raw_dir}). Pass --raw-dir.")

    xmap = unwrap_root(cfg.get("coaching_history_map") or {}, "coaching_history_map")
    hist = _mapped_history(pd.read_csv(ch_path), xmap)

    # Join each dampened candidate to its triggering coaching records within the window.
    # The window test uses coach_period (weekly bucket), matching the pipeline's driver.
    j = d.merge(hist, on=["_aid", "topic"], how="left")
    weeks_since = (j["period"] - j["coach_period"]).dt.days / 7.0
    in_window = j["coach_period"].notna() & weeks_since.between(0, periods)
    j = j[in_window].copy()
    j["weeks_since"] = ((j["period"] - j["coach_period"]).dt.days / 7.0).round(2)

    key = ["agent_id", "period", "call_type", "topic", "metric",
           "value", "benchmark", "gap", "level_score", "priority_score"]

    def _agg(g: pd.DataFrame) -> pd.Series:
        g = g.sort_values("coach_period")
        return pd.Series({
            "n_coaching_events": int(len(g)),
            "last_coached_period": g["coach_period"].max().date().isoformat(),
            "weeks_since_last": float(g["weeks_since"].min()),
            "coaching_behaviors": "; ".join(sorted(g["behavior_selected"].astype(str).unique())),
            "coaching_dates": "; ".join(sorted(dt.date().isoformat() for dt in g["coaching_date"].dropna())),
        })

    ev = j.groupby(key, dropna=False, sort=True).apply(_agg, include_groups=False).reset_index()

    ev = ev.rename(columns={"priority_score": "priority_score_dampened"})
    ev["multiplier"] = multiplier
    ev["mode"] = mode
    ev["periods_weeks"] = periods
    # multiply mode leaves the halved score in candidates; reconstruct the pre-dampen value.
    ev["priority_score_original"] = (
        ev["priority_score_dampened"] / multiplier if (mode == "multiply" and multiplier) else ev["priority_score_dampened"]
    )

    cols = ["agent_id", "period", "call_type", "topic", "metric",
            "value", "benchmark", "gap", "level_score",
            "priority_score_original", "priority_score_dampened", "multiplier", "mode", "periods_weeks",
            "n_coaching_events", "last_coached_date", "weeks_since_last",
            "coaching_behaviors", "coaching_dates"]
    ev = ev[cols].sort_values(["priority_score_original", "agent_id"], ascending=[False, True])

    out = run_dir / "dampening_evidence.csv"
    ev.to_csv(out, index=False)

    n_dampened = len(d)
    n_matched = len(ev)
    print(f"dampened (agent,topic) candidates: {n_dampened}")
    print(f"rows with coaching evidence found: {n_matched}")
    if n_matched < n_dampened:
        print(f"WARNING: {n_dampened - n_matched} dampened candidate(s) had NO matching coaching "
              f"record in the {periods}-wk window (id-format or dedup drift?).")
    print(f"distinct agents: {ev['agent_id'].nunique()}")
    print(f"wrote: {out}")


if __name__ == "__main__":
    main()
