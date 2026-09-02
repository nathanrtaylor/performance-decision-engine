"""
Machine-readable outputs (always written, alongside the dashboard).

- proposed_benchmarks.yaml : ONLY the PROPOSE rows, in benchmarks.yaml shape (the change-set to apply).
- benchmark_diff.json      : every diff row (full detail for programmatic review).
- summary.txt              : counts + a plain-text list of proposed changes.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Dict

import yaml

from . import config as C
from .compare import CompareResult


def build_proposed_mapping(result: CompareResult) -> Dict[str, dict]:
    """Collect PROPOSE rows into {metric: {default?, by_icp_client{cohort: val}}} (change-set only)."""
    out: Dict[str, dict] = {}
    for r in result.rows:
        if r.verdict != C.PROPOSE or r.new is None:
            continue
        entry = out.setdefault(r.metric, {})
        if r.cohort == "default":
            entry["default"] = r.new
        else:
            entry.setdefault("by_icp_client", {})[r.cohort] = r.new
    return out


def write_proposed_yaml(path: Path, result: CompareResult) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mapping = build_proposed_mapping(result)
    header = (
        "# Proposed benchmark CHANGES only (guardrail verdict == PROPOSE).\n"
        "# Shape matches configs/mappings/benchmarks.yaml. This is a change-set for review, NOT a full\n"
        "# replacement. Apply is a separate authorized step (recalc CLI --apply --approver).\n\n"
    )
    body = yaml.safe_dump({"benchmarks": mapping}, sort_keys=True, default_flow_style=False)
    path.write_text(header + body, encoding="utf-8")
    return path


def write_diff_json(path: Path, result: CompareResult) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"counts": result.counts, "rows": [asdict(r) for r in result.rows]}
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def write_summary_txt(path: Path, result: CompareResult, meta: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "Benchmark recalculation summary",
        f"  snapshot: {meta.get('snapshot', '-')}   window_weeks: {meta.get('window_weeks', '-')}",
        f"  PROPOSE={result.counts.get(C.PROPOSE, 0)}  HOLD={result.counts.get(C.HOLD, 0)}  "
        f"UNCHANGED={result.counts.get(C.UNCHANGED, 0)}  SKIPPED={result.counts.get(C.SKIPPED, 0)}",
        "",
        "Proposed changes:",
    ]
    proposed = [r for r in result.rows if r.verdict == C.PROPOSE]
    if not proposed:
        lines.append("  (none)")
    for r in proposed:
        old = "—" if r.old is None else f"{r.old:.4g}"
        new = "—" if r.new is None else f"{r.new:.4g}"
        lines.append(f"  {r.metric} [{r.cohort}]: {old} -> {new}   ({r.justification})")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
