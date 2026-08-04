"""
CLI: the one-command weekly operator entry point.

    python -m cde.cli.run_weekly [--raw-dir ...] [--strict-preflight]
    python -m cde.cli.run_weekly --apply-benchmarks --approver "name"   # authorized

Collapses the manual multi-command weekly sequence into one safe, ordered run:

    [optional: apply approved benchmark updates]  ->  preflight + pipeline + dashboard

The pipeline itself already runs the config/snapshot preflight and writes the dashboard, so
this wrapper's job is ordering and defaults: it auto-timestamps the output folder (via
run_pipeline's unified run-id) and, when an authorized benchmark apply is requested, runs the
apply FIRST and then re-runs the pipeline against the updated benchmarks. That ordering is the
point -- an operator can't apply new benchmarks and then accidentally ship a stale pipeline run,
because the re-run is baked in here rather than left as a remembered manual step.
"""
from __future__ import annotations

import argparse

from cde.cli import recalc_benchmarks, run_pipeline
from cde.utils.logging import get_logger

log = get_logger(__name__)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Run the full weekly coaching process in one safe, ordered command.")
    ap.add_argument("--configs-dir", type=str, default="configs", help="Path to configs directory")
    ap.add_argument("--raw-dir", type=str, default=None, help="Raw extract dir; else resolved from active.yaml data_snapshot")
    ap.add_argument("--out-dir", type=str, default=None, help="Pipeline output folder (default: outputs/runs/<timestamp>)")
    ap.add_argument("--run-id", type=str, default=None, help="Run id (default: local timestamp; also names the out-dir)")
    ap.add_argument("--strict-preflight", action="store_true", help="Treat preflight warnings as fatal (abort the run)")
    ap.add_argument("--no-preflight", action="store_true", help="Skip the preflight (not recommended for a weekly run)")
    ap.add_argument(
        "--apply-benchmarks",
        action="store_true",
        help="AUTHORIZED: apply approved benchmark updates BEFORE the pipeline run (requires --approver).",
    )
    ap.add_argument("--approver", type=str, default=None, help="Name recorded in the benchmark changelog when --apply-benchmarks is used")
    args = ap.parse_args(argv)

    # 1. Optional authorized benchmark apply -- must happen BEFORE the pipeline so the run scores
    #    against the updated benchmarks (this is the ordering the wrapper exists to guarantee).
    if args.apply_benchmarks:
        if not args.approver:
            raise SystemExit('--apply-benchmarks requires --approver "<name>"')
        log.info("Applying approved benchmark updates before the pipeline run...")
        recalc_argv = ["--configs-dir", args.configs_dir, "--apply", "--approver", args.approver]
        if args.raw_dir:
            recalc_argv += ["--raw-dir", args.raw_dir]
        recalc_benchmarks.main(recalc_argv)

    # 2. Preflight + pipeline + dashboard (run_pipeline does all three; we just pass options through).
    pipeline_argv: list[str] = ["--configs-dir", args.configs_dir]
    if args.raw_dir:
        pipeline_argv += ["--raw-dir", args.raw_dir]
    if args.out_dir:
        pipeline_argv += ["--out-dir", args.out_dir]
    if args.run_id:
        pipeline_argv += ["--run-id", args.run_id]
    if args.strict_preflight:
        pipeline_argv.append("--strict-preflight")
    if args.no_preflight:
        pipeline_argv.append("--no-preflight")

    log.info("Running preflight + pipeline + dashboard...")
    run_pipeline.main(pipeline_argv)


if __name__ == "__main__":
    main()
