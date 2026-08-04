"""CLI: validate config referential integrity (and optionally the raw snapshot).

One command the operator can run before a weekly run, or in CI, to catch a
mis-registered metric before it becomes wrong coaching.

    python -m cde.cli.check_config                         # lint active configs
    python -m cde.cli.check_config --raw-dir <snapshot>    # + raw-snapshot preflight
    python -m cde.cli.check_config --strict                # warnings fail too

Exit codes: 0 = clean (or warnings-only without --strict); 1 = errors (or any
warning under --strict).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cde.governance.config_lint import lint_config, preflight_snapshot
from cde.governance.versioning import resolve_active_config, resolve_raw_export_dir


def run_preflight(configs_dir: Path, *, raw_dir: Path | None = None, include_snapshot: bool = True):
    """Resolve config, lint it, and (optionally) preflight the raw snapshot.

    Returns (LintReport, resolved_config). Shared by the CLI and run_pipeline's
    built-in preflight so both apply exactly the same checks.
    """
    config = resolve_active_config(configs_dir)
    report = lint_config(config)
    if include_snapshot:
        target = raw_dir
        if target is None:
            try:
                target = resolve_raw_export_dir(configs_dir, config)
            except Exception as e:  # noqa: BLE001 - missing snapshot config is itself worth reporting
                report.warnings.append(f"snapshot: could not resolve raw dir for preflight ({e})")
                target = None
        if target is not None:
            report.merge(preflight_snapshot(Path(target), config))
    return report, config


def main() -> int:
    ap = argparse.ArgumentParser(description="Lint CDE config cross-references + raw snapshot")
    ap.add_argument("--configs-dir", default="configs", help="Path to configs directory")
    ap.add_argument("--raw-dir", default=None, help="Raw snapshot to preflight (default: resolved from active.yaml)")
    ap.add_argument("--no-snapshot", action="store_true", help="Config checks only; skip the raw-snapshot preflight")
    ap.add_argument("--strict", action="store_true", help="Treat warnings as errors (exit 1 on any warning)")
    args = ap.parse_args()

    report, _ = run_preflight(
        Path(args.configs_dir),
        raw_dir=Path(args.raw_dir) if args.raw_dir else None,
        include_snapshot=not args.no_snapshot,
    )
    print(report.render(strict=args.strict))
    return 1 if (report.errors or (args.strict and report.warnings)) else 0


if __name__ == "__main__":
    sys.exit(main())
