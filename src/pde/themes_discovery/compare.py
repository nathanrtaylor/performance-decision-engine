"""
Compare proposed candidate themes against the current themes.yaml and attach a verdict per row.

A candidate is "existing" when its members substantially overlap an already-curated theme
(Jaccard over member sets); otherwise it is "new". Nothing is written here — this only builds the
review record consumed by emit/dashboard.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import config as C
from .config import DiscoveryThresholds
from .guardrails import evaluate
from .recompute import CandidateTheme

_OVERLAP_MATCH = 0.5  # Jaccard >= this vs an existing theme => treated as a match to that theme


@dataclass
class ThemeProposalRow:
    members: List[str]
    cohorts: List[str]
    mean_corr: float
    coverage: float
    n_min: int
    verdict: str
    justification: str
    is_new: bool
    matched_theme: Optional[str] = None
    checks: Dict[str, bool] = field(default_factory=dict)


@dataclass
class CompareResult:
    rows: List[ThemeProposalRow]
    counts: Dict[str, int]


def _load_existing_themes(config: Dict[str, Any]) -> Dict[str, List[str]]:
    raw = config.get("themes") or {}
    inner = raw.get("themes", raw) if isinstance(raw, dict) else {}
    out: Dict[str, List[str]] = {}
    if isinstance(inner, dict):
        for name, spec in inner.items():
            if isinstance(spec, dict) and spec.get("members"):
                out[str(name)] = [str(m) for m in spec["members"]]
    return out


def _best_overlap(members: List[str], existing: Dict[str, List[str]]) -> tuple[Optional[str], float]:
    best_name, best_j = None, 0.0
    ms = set(members)
    for name, mem in existing.items():
        es = set(mem)
        union = ms | es
        j = len(ms & es) / len(union) if union else 0.0
        if j > best_j:
            best_name, best_j = name, j
    return best_name, best_j


def compare(
    themes: List[CandidateTheme], config: Dict[str, Any], thr: DiscoveryThresholds
) -> CompareResult:
    existing = _load_existing_themes(config)
    rows: List[ThemeProposalRow] = []
    counts: Dict[str, int] = {C.PROPOSE: 0, C.HOLD: 0, C.SKIPPED: 0}

    for t in themes:
        report = evaluate(t, thr)
        matched, j = _best_overlap(t.members, existing)
        is_new = j < _OVERLAP_MATCH
        rows.append(ThemeProposalRow(
            members=t.members,
            cohorts=t.cohorts,
            mean_corr=t.mean_corr,
            coverage=t.coverage,
            n_min=t.n_min,
            verdict=report.verdict,
            justification=report.reason,
            is_new=is_new,
            matched_theme=(None if is_new else matched),
            checks=report.checks,
        ))
        counts[report.verdict] = counts.get(report.verdict, 0) + 1

    return CompareResult(rows=rows, counts=counts)
