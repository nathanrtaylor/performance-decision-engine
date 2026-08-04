"""
CLI: discover candidate coaching themes from population co-movement (propose-only by default).

    python -m cde.cli.discover_themes --configs-dir configs \
        --out-dir outputs/theme_discovery/<id> [--raw-dir ...] [--apply --approver "name"]

Reads the same config + extract the pipeline reads, but does NOT touch the pipeline. Writes a
dashboard + proposed themes change-set + diff. A theme enters the engine ONLY when a human SME
merges it into configs/mappings/themes.yaml; even --apply defers every proposal for manual merge.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from cde.governance.versioning import resolve_active_config
from cde.utils.logging import get_logger

from cde.themes_discovery.compare import compare
from cde.themes_discovery.config import DiscoveryThresholds, HOLD, PROPOSE, SKIPPED
from cde.themes_discovery.dashboard import write_discovery_dashboard
from cde.themes_discovery.emit import write_diff_json, write_proposed_yaml, write_summary_txt
from cde.themes_discovery.prep import build_bad_axis_by_cohort, load_latest_extract, prep_frames
from cde.themes_discovery.recompute import cluster_into_themes, compute_comovement

log = get_logger(__name__)


def main() -> None:
    ap = argparse.ArgumentParser(description="Discover candidate coaching themes (propose-only).")
    ap.add_argument("--configs-dir", type=str, default="configs", help="Path to configs directory")
    ap.add_argument("--out-dir", type=str, default=None, help="Output folder (default outputs/theme_discovery/<ts>)")
    ap.add_argument("--raw-dir", type=str, default=None, help="Raw extract dir; else resolved from active.yaml data_snapshot")
    ap.add_argument("--apply", action="store_true", help="Record a governance review entry (themes still merged by hand)")
    ap.add_argument("--approver", type=str, default=None, help="Name recorded in the changelog when --apply is used")
    args = ap.parse_args()

    configs_dir = Path(args.configs_dir)
    out_dir = Path(args.out_dir) if args.out_dir else Path("outputs/theme_discovery") / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    config = resolve_active_config(configs_dir)
    thr = DiscoveryThresholds.from_config(config)

    raw = load_latest_extract(configs_dir, config, Path(args.raw_dir) if args.raw_dir else None)
    prepped = prep_frames(raw, config)
    matrices = build_bad_axis_by_cohort(prepped, config)
    pairs = compute_comovement(matrices, thr)
    themes = cluster_into_themes(pairs, thr)
    result = compare(themes, config, thr)

    meta = {"snapshot": prepped.snapshot_id, "window_weeks": len(prepped.window_weeks) or "-"}

    dash = write_discovery_dashboard(out_dir / "dashboard.html", result, meta)
    write_proposed_yaml(out_dir / "proposed_themes.yaml", result)
    write_diff_json(out_dir / "theme_diff.json", result)
    write_summary_txt(out_dir / "summary.txt", result, meta)

    c = result.counts
    print(
        f"Theme discovery [{meta['snapshot']}]: "
        f"PROPOSE={c.get(PROPOSE, 0)} HOLD={c.get(HOLD, 0)} SKIPPED={c.get(SKIPPED, 0)} "
        f"(cohorts analyzed: {len(matrices)})"
    )
    print(f"Dashboard: {dash}")

    if args.apply:
        from cde.themes_discovery.apply import apply_themes

        report = apply_themes(
            configs_dir / "mappings" / "themes.yaml", result,
            changelog_path=configs_dir / "governance" / "changelog.md",
            approver=args.approver, snapshot=meta["snapshot"],
        )
        print(f"DEFERRED {len(report.deferred)} theme(s) for manual SME merge from proposed_themes.yaml:")
        for d in report.deferred:
            print(f"  [deferred] {d}")
        print("themes.yaml was NOT modified (themes are human-added by design).")
    else:
        print("Propose-only: themes.yaml not modified. A human SME merges proposals from proposed_themes.yaml.")


if __name__ == "__main__":
    main()
