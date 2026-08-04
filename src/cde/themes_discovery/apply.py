"""
"Apply" for theme discovery — deliberately human-in-the-loop.

Per the governance rule that a theme enters the engine ONLY when a human SME adds it, this NEVER
edits configs/mappings/themes.yaml. Even with --apply, every proposed theme is DEFERRED for manual
merge from proposed_themes.yaml; the only side effect is a governance changelog entry recording the
review + intent. Imported lazily so a propose-only run never loads it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import config as C
from .compare import CompareResult


@dataclass
class ApplyReport:
    applied: List[str] = field(default_factory=list)          # always empty: themes are human-added
    deferred: List[str] = field(default_factory=list)         # "members (new|~existing)"


def apply_themes(
    themes_path: Path,
    result: CompareResult,
    *,
    changelog_path: Optional[Path] = None,
    approver: Optional[str] = None,
    snapshot: str = "-",
) -> ApplyReport:
    """
    Defer every PROPOSE theme for manual SME merge (themes.yaml is left byte-identical).
    Records a changelog entry noting the review when a changelog path is given.
    """
    report = ApplyReport()
    for r in result.rows:
        if r.verdict != C.PROPOSE:
            continue
        tag = "new" if r.is_new else f"~ existing '{r.matched_theme}'"
        report.deferred.append(f"{', '.join(r.members)} ({tag})")

    if report.deferred and changelog_path is not None:
        _append_changelog(Path(changelog_path), report, approver=approver, snapshot=snapshot)
    return report


def _append_changelog(path: Path, report: ApplyReport, *, approver: Optional[str], snapshot: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    block = [
        f"\n## {stamp} — theme discovery review",
        f"- snapshot: {snapshot}",
        f"- approver: {approver or '(unspecified)'}",
        "- themes are human-added only; the following were DEFERRED for manual merge "
        "from proposed_themes.yaml:",
        *[f"  - {d}" for d in report.deferred],
    ]
    existing = path.read_text(encoding="utf-8") if path.exists() else "# Benchmark changelog\n"
    path.write_text(existing + "\n".join(block) + "\n", encoding="utf-8")
