"""
Machine-readable outputs (always written, alongside the dashboard).

- proposed_themes.yaml : ONLY the PROPOSE rows, in themes.yaml shape (a change-set for SME review).
- theme_diff.json      : every proposal row (full detail for programmatic review).
- summary.txt          : counts + a plain-text list of proposed themes.

proposed_themes.yaml is a SUGGESTION for a human SME to merge by hand. Theme names and
conversation_type are placeholders the SME renames — a theme enters the engine ONLY when a human
adds it to configs/mappings/themes.yaml.
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
    """Collect PROPOSE rows into {suggested_name: {members, conversation_type}} (change-set only)."""
    out: Dict[str, dict] = {}
    i = 0
    for r in result.rows:
        if r.verdict != C.PROPOSE:
            continue
        i += 1
        name = f"Candidate Theme {i}"
        out[name] = {
            "members": list(r.members),
            "conversation_type": "Performance Coaching",  # SME: set the right type
        }
    return out


def write_proposed_yaml(path: Path, result: CompareResult) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mapping = build_proposed_mapping(result)
    header = (
        "# PROPOSED coaching themes (guardrail verdict == PROPOSE) from population co-movement.\n"
        "# Shape matches configs/mappings/themes.yaml. This is a SUGGESTION for review, NOT applied.\n"
        "# A human SME must rename each theme, confirm/adjust members + conversation_type, and merge\n"
        "# into configs/mappings/themes.yaml by hand. Discovery never adds themes automatically.\n\n"
    )
    body = yaml.safe_dump({"themes": mapping}, sort_keys=True, default_flow_style=False)
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
        "Theme discovery summary",
        f"  snapshot: {meta.get('snapshot', '-')}   window_weeks: {meta.get('window_weeks', '-')}",
        f"  PROPOSE={result.counts.get(C.PROPOSE, 0)}  HOLD={result.counts.get(C.HOLD, 0)}  "
        f"SKIPPED={result.counts.get(C.SKIPPED, 0)}",
        "",
        "Proposed themes (for SME review):",
    ]
    proposed = [r for r in result.rows if r.verdict == C.PROPOSE]
    if not proposed:
        lines.append("  (none)")
    for i, r in enumerate(proposed, 1):
        tag = "new" if r.is_new else f"~ existing '{r.matched_theme}'"
        lines.append(
            f"  Candidate Theme {i} [{tag}]: {', '.join(r.members)}  "
            f"(mean r={r.mean_corr:.2f}, coverage={r.coverage:.0%}, n>={r.n_min})"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
