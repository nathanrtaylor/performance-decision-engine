"""CLI: generate the interactive expert-coaching dashboard from a saved run.

The reusable rendering core lives in ``cde.reporting.expert_dashboard`` (also called
by the pipeline). This wrapper loads a saved run from disk and can rebuild receipts
from the run's CSVs with the current engine code — useful for previewing narrative
changes against a historical run whose ``decision_receipts.jsonl`` predates them.

Usage:
    python tools/expert_dashboard.py [RUN_DIR] [--agents PATH] [--from-jsonl] [--out PATH]

Defaults:
    RUN_DIR  = outputs/runs/2026-08-12_fullrun
    --agents = data/raw/weekly/latest/agents.csv
    --out    = <RUN_DIR>/expert_dashboard.html
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from cde.reporting.expert_dashboard import (
    agents_map_from_df,
    build_experts,
    coaching_history_map_from_df,
    render_html,
)


def _load_agents_df(agents_csv: Path) -> Optional[pd.DataFrame]:
    return pd.read_csv(agents_csv, dtype=str) if agents_csv.exists() else None


def _load_coaching_history_df(path: Path) -> Optional[pd.DataFrame]:
    return pd.read_csv(path, dtype=str) if path.exists() else None


def _prov_from_jsonl(run_dir: Path) -> Dict[str, Any]:
    """Read provenance + config_hash from the run's saved receipts (for accurate header)."""
    path = run_dir / "decision_receipts.jsonl"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                r = json.loads(line)
                return {"prov": r.get("provenance") or {}, "config_hash": r.get("config_hash")}
    return {}


def _read_run_csv(run_dir: Path, name: str) -> Optional[pd.DataFrame]:
    p = run_dir / name
    if not p.exists():
        return None
    df = pd.read_csv(p)
    if "period" in df.columns:
        df["period"] = pd.to_datetime(df["period"], format="mixed")
    return df


def rebuild_receipts(run_dir: Path, configs_dir: Path) -> List[Dict[str, Any]]:
    """Regenerate receipts from the run's saved CSVs using the CURRENT engine code."""
    from cde.governance.versioning import resolve_active_config
    from cde.engine.select import select_recommendations
    from cde.engine.receipts import build_receipts

    candidates = _read_run_csv(run_dir, "topic_candidates.csv")
    scores = _read_run_csv(run_dir, "scores_windowed.csv")
    eligible = _read_run_csv(run_dir, "eligible_signals.csv")
    excluded = _read_run_csv(run_dir, "excluded_signals.csv")
    if candidates is None or scores is None or eligible is None:
        raise FileNotFoundError("missing CSV inputs for rebuild")

    config = resolve_active_config(configs_dir)
    info = _prov_from_jsonl(run_dir)
    prov = info.get("prov") or {}
    config["meta"] = {
        **(config.get("meta") or {}),
        "version": prov.get("config_version") or (config.get("meta") or {}).get("version"),
        "data_snapshot": prov.get("data_snapshot"),
        "engine_version": prov.get("engine_version", "0.1.0"),
        "config_hash": info.get("config_hash"),
    }

    recs, detail = select_recommendations(candidates, eligible, scores, config)
    receipts = build_receipts(
        recs, candidates, eligible, scores, config,
        excluded_signals=excluded, selection_detail=detail,
    )
    return receipts.to_dict(orient="records")


def read_receipts_jsonl(run_dir: Path) -> List[Dict[str, Any]]:
    path = run_dir / "decision_receipts.jsonl"
    out: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _load_meta(run_dir: Path) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    mf = run_dir / "manifest.json"
    if mf.exists():
        try:
            meta["run_id"] = json.loads(mf.read_text(encoding="utf-8")).get("run_id")
        except Exception:
            pass
    meta.setdefault("run_id", run_dir.name)
    return meta


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dir", nargs="?", default="outputs/runs/2026-08-12_fullrun")
    ap.add_argument("--agents", default="data/raw/weekly/latest/agents.csv")
    ap.add_argument("--coaching-history", default="data/raw/weekly/latest/coaching_history.csv",
                    help="Raw coaching_history.csv for the 'Recent coaching history' block "
                         "(missing file simply omits the block).")
    ap.add_argument("--configs", default="configs")
    ap.add_argument("--from-jsonl", action="store_true",
                    help="Use the run's saved decision_receipts.jsonl as-is instead of "
                         "rebuilding receipts from CSVs with the current engine code.")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    run_dir = Path(args.run_dir)
    out = Path(args.out) if args.out else run_dir / "expert_dashboard.html"

    agents_df = _load_agents_df(Path(args.agents))
    amap = agents_map_from_df(agents_df)
    chmap = coaching_history_map_from_df(_load_coaching_history_df(Path(args.coaching_history)))

    if args.from_jsonl:
        print("Reading saved decision_receipts.jsonl ...", flush=True)
        receipts = read_receipts_jsonl(run_dir)
    else:
        try:
            print("Rebuilding receipts from run CSVs with current engine code "
                  "(loads eligible_signals.csv; ~30-60s) ...", flush=True)
            receipts = rebuild_receipts(run_dir, Path(args.configs))
        except FileNotFoundError as ex:
            print(f"  rebuild unavailable ({ex}); falling back to saved jsonl.", flush=True)
            receipts = read_receipts_jsonl(run_dir)

    experts = build_experts(receipts, amap, chmap)
    prov = experts[0]["prov"] if experts else {}
    meta = _load_meta(run_dir)
    meta.update({
        "data_snapshot": prov.get("ds"),
        "engine_version": prov.get("ev"),
        "config_version": prov.get("cv"),
        "config_hash": prov.get("ch"),
        "generated": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
    })

    out.write_text(render_html(experts, meta), encoding="utf-8")

    n = len(experts)
    matched = sum(1 for e in experts if e["icp"] != "(unknown)")
    print(f"Experts: {n}  | icp/mascot matched: {matched} ({matched/n*100:.1f}%)" if n else "No experts.")
    print(f"ICP clients: {sorted({e['icp'] for e in experts})}")
    print(f"Wrote {out}  ({out.stat().st_size/1_048_576:.2f} MB)")


if __name__ == "__main__":
    main()
