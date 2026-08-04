"""
CLI: recalculate benchmarks from the latest extract (propose-only by default).

    python -m cde.cli.recalc_benchmarks --configs-dir configs \
        --out-dir outputs/benchmark_recalc/<id> [--raw-dir ...] [--apply --approver "name"]

Reads the same config + extract the pipeline reads, but does NOT touch the pipeline. Writes a
dashboard + proposed change-set + diff. Applies to benchmarks.yaml ONLY with --apply (authorized).
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from cde.governance.versioning import resolve_active_config
from cde.utils.logging import get_logger

from cde.benchmarks_recalc.compare import compare
from cde.benchmarks_recalc.config import RecalcThresholds, PROPOSE, HOLD, UNCHANGED, SKIPPED
from cde.benchmarks_recalc.dashboard import write_recalc_dashboard
from cde.benchmarks_recalc.emit import write_diff_json, write_proposed_yaml, write_summary_txt
from cde.benchmarks_recalc.prep import load_latest_extract, prep_frames
from cde.benchmarks_recalc.recompute import recompute_all

log = get_logger(__name__)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Recalculate benchmarks from the latest extract (propose-only).")
    ap.add_argument("--configs-dir", type=str, default="configs", help="Path to configs directory")
    ap.add_argument("--out-dir", type=str, default=None, help="Output folder (default outputs/benchmark_recalc/<ts>)")
    ap.add_argument("--raw-dir", type=str, default=None, help="Raw extract dir; else resolved from active.yaml data_snapshot")
    ap.add_argument("--apply", action="store_true", help="AUTHORIZED: write value updates into benchmarks.yaml")
    ap.add_argument("--approver", type=str, default=None, help="Name recorded in the changelog when --apply is used")
    args = ap.parse_args(argv)

    configs_dir = Path(args.configs_dir)
    out_dir = Path(args.out_dir) if args.out_dir else Path("outputs/benchmark_recalc") / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    config = resolve_active_config(configs_dir)
    thr = RecalcThresholds.from_config(config)

    raw = load_latest_extract(configs_dir, config, Path(args.raw_dir) if args.raw_dir else None)
    prepped = prep_frames(raw, config)
    candidates = recompute_all(prepped, thr)
    result = compare(candidates, prepped, config, thr)

    meta = {"snapshot": prepped.snapshot_id, "window_weeks": len(prepped.window_weeks) or "-"}

    # Always: dashboard + machine-readable change-set + diff + summary.
    dash = write_recalc_dashboard(out_dir / "dashboard.html", result, meta)
    write_proposed_yaml(out_dir / "proposed_benchmarks.yaml", result)
    write_diff_json(out_dir / "benchmark_diff.json", result)
    write_summary_txt(out_dir / "summary.txt", result, meta)

    c = result.counts
    print(
        f"Benchmark recalc [{meta['snapshot']}]: "
        f"PROPOSE={c.get(PROPOSE,0)} HOLD={c.get(HOLD,0)} "
        f"UNCHANGED={c.get(UNCHANGED,0)} SKIPPED={c.get(SKIPPED,0)}"
    )
    print(f"Dashboard: {dash}")

    if args.apply:
        # Imported lazily so a propose-only run never loads the writer.
        from cde.benchmarks_recalc.apply import apply_benchmarks

        # Standard location of the benchmarks mapping (active.yaml mappings.benchmarks).
        bpath = configs_dir / "mappings" / "benchmarks.yaml"
        report = apply_benchmarks(
            bpath, result,
            changelog_path=configs_dir / "governance" / "changelog.md",
            approver=args.approver, snapshot=meta["snapshot"],
        )
        print(f"APPLIED {len(report.applied)} value update(s) to {bpath}")
        for a in report.applied:
            print(f"  [applied] {a}")
        if report.skipped_structural:
            print(f"DEFERRED {len(report.skipped_structural)} structural change(s) -> merge from proposed_benchmarks.yaml:")
            for s in report.skipped_structural:
                print(f"  [deferred] {s}")
    else:
        print("Propose-only: benchmarks.yaml not modified. Re-run with --apply --approver \"<name>\" to apply.")


if __name__ == "__main__":
    main()
