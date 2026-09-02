"""CLI: report the training program's coverage -- what's defined vs still TODO.

Companion to ``check_config`` for the training domain. Loads
``configs/training/training_program.yaml`` and the training metric catalog, and
reports which blocks still lack a gate test-call, enumerated components, or an
expected completion day, plus any ``develops:`` skill that isn't a real cataloged
metric and any cataloged skill no block develops yet.

    python -m pde.cli.check_training_program
    python -m pde.cli.check_training_program --strict     # TODOs (warnings) fail too

Exit codes: 0 = clean (or warnings-only without --strict); 1 = errors (a develops
entry referencing an unknown metric, or a structural problem) or, with --strict,
any warning.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from pde.governance.versioning import resolve_active_config
from pde.training.program import load_program, program_coverage


def _catalog_skills(configs_dir: Path) -> set:
    cfg = resolve_active_config(configs_dir)
    mc = cfg.get("metric_catalog") or {}
    mc = mc.get("metric_catalog", mc) if isinstance(mc, dict) else {}
    return set((mc.get("metrics") or {}).keys())


def main() -> int:
    ap = argparse.ArgumentParser(description="Report training program coverage (defined vs TODO)")
    ap.add_argument("--configs-dir", default="configs/training", help="Training configs directory")
    ap.add_argument("--program", default=None, help="Path to training_program.yaml (default: <configs-dir>/training_program.yaml)")
    ap.add_argument("--strict", action="store_true", help="Treat TODO warnings as errors")
    args = ap.parse_args()

    configs_dir = Path(args.configs_dir)
    program_path = Path(args.program) if args.program else configs_dir / "training_program.yaml"

    program = load_program(program_path)
    catalog = _catalog_skills(configs_dir)
    cov = program_coverage(program, catalog)

    errors: list[str] = []
    warnings: list[str] = []

    # ---- structural errors ----
    ids = [b.id for b in program.blocks]
    dup_ids = [i for i, n in Counter(ids).items() if n > 1]
    if dup_ids:
        errors.append(f"duplicate block id(s): {dup_ids}")
    dup_order = [o for o, n in Counter(b.order for b in program.blocks).items() if n > 1]
    if dup_order:
        errors.append(f"duplicate block order value(s): {dup_order}")
    if cov["develops_unknown"]:
        errors.append(f"develops references metric(s) not in the catalog: {cov['develops_unknown']}")

    # ---- TODO warnings ----
    if cov["blocks_missing_gate"]:
        warnings.append(f"{len(cov['blocks_missing_gate'])} block(s) have no gate test_call (TODO): {cov['blocks_missing_gate']}")
    if cov["blocks_missing_components"]:
        warnings.append(f"{len(cov['blocks_missing_components'])} block(s) have no enumerated components (TODO): {cov['blocks_missing_components']}")
    if cov["blocks_missing_expected_day"]:
        warnings.append(f"{len(cov['blocks_missing_expected_day'])} block(s) have no expected_completion_day: {cov['blocks_missing_expected_day']}")
    if cov["unmapped_catalog_skills"]:
        warnings.append(f"{len(cov['unmapped_catalog_skills'])} cataloged skill(s) not developed by any block: {cov['unmapped_catalog_skills']}")

    # ---- render ----
    print(f"training program: {program.name!r} -- {cov['n_blocks']} blocks, "
          f"{cov['n_skills_mapped']} skills mapped, pace {program.pace_hours_per_day} h/day")
    for e in errors:
        print(f"  ERROR  {e}")
    for w in warnings:
        print(f"  WARN   {w}")
    if not errors and not warnings:
        print("  OK  program structure fully defined")

    n_err = len(errors)
    n_warn = len(warnings)
    verdict = "FAIL" if (n_err or (args.strict and n_warn)) else "PASS"
    print(f"training-program-check {verdict}: {n_err} error(s), {n_warn} warning(s)")
    return 1 if (n_err or (args.strict and n_warn)) else 0


if __name__ == "__main__":
    sys.exit(main())
