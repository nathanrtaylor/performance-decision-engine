"""Throwaway: preview NEW vs OLD receipt narratives on a saved run WITHOUT the full pipeline.

Re-runs only selection + receipts from the CSVs already saved in a run folder, and diffs the
narratives against the run's original decision_receipts.jsonl.

Usage:
    python tools/preview_receipts.py outputs/runs/2026-08-12_fullrun
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from cde.governance.versioning import resolve_active_config
from cde.engine.select import select_recommendations
from cde.engine.receipts import build_receipts


def _load(run_dir: Path):
    def rd(name, **kw):
        return pd.read_csv(run_dir / name, **kw)

    candidates = rd("topic_candidates.csv")
    scores = rd("scores_windowed.csv")
    excluded = rd("excluded_signals.csv") if (run_dir / "excluded_signals.csv").exists() else None
    eligible = rd("eligible_signals.csv")

    for df in (candidates, scores, excluded, eligible):
        if df is not None and "period" in df.columns:
            df["period"] = pd.to_datetime(df["period"], format="mixed")
    return candidates, scores, excluded, eligible


def _old_narratives(run_dir: Path):
    """Map (agent_id, tier) -> narrative dict from the ORIGINAL receipts jsonl."""
    out = {}
    with (run_dir / "decision_receipts.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            out[(str(r.get("agent_id")), r.get("tier"))] = r.get("narrative", {})
    return out


def main():
    run_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "outputs/runs/2026-08-12_fullrun")
    config = resolve_active_config(Path("configs"))

    print(f"Loading CSVs from {run_dir} ...", flush=True)
    candidates, scores, excluded, eligible = _load(run_dir)

    print("Re-running selection (rebuilds selection_detail + displaced-single alt cols) ...", flush=True)
    recs, detail = select_recommendations(candidates, eligible, scores, config)

    print("Building receipts with NEW narratives ...", flush=True)
    receipts = build_receipts(
        recs, candidates, eligible, scores, config,
        excluded_signals=excluded, selection_detail=detail,
    )

    old = _old_narratives(run_dir)

    # Sample a few agents per tier.
    for tier in ("single", "theme", "break_glass"):
        sub = receipts[receipts["tier"] == tier].head(2)
        for _, r in sub.iterrows():
            key = (str(r["agent_id"]), tier)
            print("\n" + "=" * 100)
            print(f"TIER={tier}  agent={r['agent_id']}  topic={r['recommended_topic']}")
            o = old.get(key, {})
            n = r["narrative"]
            for sec in ("why_this", "why_now", "why_not_others"):
                print(f"\n[{sec}]")
                print(f"  OLD: {o.get(sec, '(n/a)')}")
                print(f"  NEW: {n.get(sec, '(n/a)')}")

    # Guard: no internal score tokens leak into any narrative.
    bad = []
    tokens = ("priority_score", "risk_score", "trend_score", "level_score", "confidence_score")
    for _, r in receipts.iterrows():
        for v in (r["narrative"] or {}).values():
            if isinstance(v, str) and any(t in v for t in tokens):
                bad.append((r["agent_id"], r["tier"], v))
    print("\n" + "=" * 100)
    print(f"Total receipts: {len(receipts)} | internal-score leaks in prose: {len(bad)}")
    for b in bad[:5]:
        print("  LEAK:", b)


if __name__ == "__main__":
    main()
