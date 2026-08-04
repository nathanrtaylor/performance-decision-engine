"""
Authorized apply: surgically update existing benchmark values in benchmarks.yaml.

Only imported/called when the CLI is run with ``--apply``. pyyaml round-tripping would strip the
curated methodology comments, so this does a LINE-ORIENTED replacement: for each PROPOSE row it
rewrites only the scalar after ``default:`` or the ``<cohort>:`` line under ``by_icp_client:``,
leaving every comment and untouched line byte-identical.

v1 scope = value updates of EXISTING keys only. Structural additions (a brand-new cohort/split or a
metric absent from the file) are NOT written here; they are reported back for manual merge from
proposed_benchmarks.yaml. A changelog entry is appended for the applied changes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config as C
from .compare import CompareResult


@dataclass
class ApplyReport:
    applied: List[str] = field(default_factory=list)          # "metric [cohort]: old -> new"
    skipped_structural: List[str] = field(default_factory=list)
    not_found: List[str] = field(default_factory=list)


def _fmt_value(v: float) -> str:
    return f"{float(v):.4g}"


def _metric_spans(lines: List[str]) -> Dict[str, Tuple[int, int]]:
    """metric name -> [start, end) line span within the `benchmarks:` mapping (indent-2 keys)."""
    spans: Dict[str, Tuple[int, int]] = {}
    header = re.compile(r"^  ([A-Za-z0-9_]+):\s*(#.*)?$")
    starts: List[Tuple[int, str]] = [
        (i, m.group(1)) for i, ln in enumerate(lines) if (m := header.match(ln))
    ]
    for idx, (line_no, name) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        spans[name] = (line_no, end)
    return spans


def _replace_scalar(line: str, new_value: float) -> str:
    """Replace the scalar in a `key: <value>  # comment` line, preserving indent/key/comment."""
    m = re.match(r"^(\s*[^:]+:\s*)([^#\n]*?)(\s*)(#.*)?$", line.rstrip("\n"))
    if not m:
        return line
    prefix, _old, gap, comment = m.group(1), m.group(2), m.group(3), m.group(4) or ""
    tail = (gap + comment) if comment else ""
    newline = "\n" if line.endswith("\n") else ""
    return f"{prefix}{_fmt_value(new_value)}{tail}{newline}"


def _apply_one(lines: List[str], span: Tuple[int, int], cohort: str, new_value: float) -> Optional[bool]:
    """Edit lines in-place for one (cohort,value). Returns True if edited, None if key absent."""
    start, end = span
    if cohort == "default":
        for i in range(start, end):
            if re.match(r"^\s{4}default:\s", lines[i]):
                lines[i] = _replace_scalar(lines[i], new_value)
                return True
        return None
    # by_icp_client cohort
    in_block = False
    for i in range(start, end):
        if re.match(r"^\s{4}by_icp_client:\s*(#.*)?$", lines[i]):
            in_block = True
            continue
        if in_block:
            if re.match(r"^\s{0,4}\S", lines[i]):  # dedented back to metric/default level -> block ended
                break
            if re.match(rf"^\s{{6}}{re.escape(cohort)}:\s", lines[i]):
                lines[i] = _replace_scalar(lines[i], new_value)
                return True
    return None


def apply_benchmarks(
    benchmarks_path: Path,
    result: CompareResult,
    *,
    changelog_path: Optional[Path] = None,
    approver: Optional[str] = None,
    snapshot: str = "-",
) -> ApplyReport:
    benchmarks_path = Path(benchmarks_path)
    lines = benchmarks_path.read_text(encoding="utf-8").splitlines(keepends=True)
    spans = _metric_spans(lines)
    report = ApplyReport()

    for r in result.rows:
        if r.verdict != C.PROPOSE or r.new is None:
            continue
        if r.metric not in spans:
            report.skipped_structural.append(f"{r.metric} [{r.cohort}] (metric not in file)")
            continue
        if r.is_new_cohort:
            report.skipped_structural.append(f"{r.metric} [{r.cohort}] (new cohort split - manual merge)")
            continue
        edited = _apply_one(lines, spans[r.metric], r.cohort, r.new)
        if edited:
            old = "—" if r.old is None else f"{r.old:.4g}"
            report.applied.append(f"{r.metric} [{r.cohort}]: {old} -> {_fmt_value(r.new)}")
        else:
            report.not_found.append(f"{r.metric} [{r.cohort}] (key not found)")

    if report.applied:
        benchmarks_path.write_text("".join(lines), encoding="utf-8")
        if changelog_path is not None:
            _append_changelog(Path(changelog_path), report, approver=approver, snapshot=snapshot)
    return report


def _append_changelog(path: Path, report: ApplyReport, *, approver: Optional[str], snapshot: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    block = [
        f"\n## {stamp} — benchmark recalculation apply",
        f"- snapshot: {snapshot}",
        f"- approver: {approver or '(unspecified)'}",
        "- applied:",
        *[f"  - {a}" for a in report.applied],
    ]
    if report.skipped_structural:
        block.append("- deferred (manual merge from proposed_benchmarks.yaml):")
        block.extend(f"  - {s}" for s in report.skipped_structural)
    existing = path.read_text(encoding="utf-8") if path.exists() else "# Governance changelog\n"
    path.write_text(existing + "\n".join(block) + "\n", encoding="utf-8")
