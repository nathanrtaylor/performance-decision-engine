"""Export per-agent dampening evidence for a saved run (post-hoc).

Thin CLI wrapper around cde.prioritization.dampening_evidence.build_dampening_evidence — the same
function the pipeline now calls to emit dampening_evidence.csv. Use this to (re)generate the
evidence for an OLD run, or a run produced before the pipeline wired it in.

Reads the run's topic_candidates.csv (dampened flag) and the raw coaching_history.csv the run
consumed (from the run manifest's raw_dir, or --raw-dir), and writes
<run_dir>/dampening_evidence.csv.

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
from cde.prioritization.dampening_evidence import build_dampening_evidence

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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--raw-dir", type=Path, default=None,
                    help="Override coaching_history source dir (default: from run manifest).")
    ap.add_argument("--configs", type=Path, default=CONFIGS)
    args = ap.parse_args()

    run_dir: Path = args.run_dir
    cfg = resolve_active_config(args.configs)

    cand_path = run_dir / "topic_candidates.csv"
    if not cand_path.exists():
        raise SystemExit(f"{cand_path} not found.")
    candidates = pd.read_csv(cand_path)

    raw_dir = args.raw_dir or _raw_dir_from_manifest(run_dir)
    ch_path = (raw_dir / "coaching_history.csv") if raw_dir else None
    if not ch_path or not ch_path.exists():
        raise SystemExit(f"coaching_history.csv not found (looked in {raw_dir}). Pass --raw-dir.")
    coaching_raw = pd.read_csv(ch_path)

    ev = build_dampening_evidence(candidates, coaching_raw, cfg)

    out = run_dir / "dampening_evidence.csv"
    ev.to_csv(out, index=False)

    n_dampened = int((candidates.get("dampened") == True).sum()) if "dampened" in candidates.columns else 0  # noqa: E712
    print(f"dampened (agent,topic) candidates: {n_dampened}")
    print(f"evidence rows written: {len(ev)}  (distinct agents: {ev['agent_id'].nunique() if not ev.empty else 0})")
    if n_dampened and len(ev) < n_dampened:
        print(f"NOTE: {n_dampened - len(ev)} dampened candidate(s) had no matching coaching event in the "
              f"window (id-format/dedup drift or coaching outside the window).")
    print(f"wrote: {out}")


if __name__ == "__main__":
    main()
