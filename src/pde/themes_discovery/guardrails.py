"""
Guardrails: turn a CandidateTheme into a PROPOSE / HOLD / SKIPPED verdict with a reason.

Clustering already applied the correlation + coverage thresholds to form the theme; these guardrails
re-check theme-level sufficiency so the dashboard can justify each verdict explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

from .config import DiscoveryThresholds, HOLD, PROPOSE, SKIPPED
from .recompute import CandidateTheme


@dataclass(frozen=True)
class GuardrailReport:
    verdict: str
    reason: str
    checks: Dict[str, bool] = field(default_factory=dict)


def evaluate(theme: CandidateTheme, thr: DiscoveryThresholds) -> GuardrailReport:
    checks = {
        "size_ok": thr.min_theme_size <= len(theme.members) <= thr.max_theme_size,
        "sample_ok": theme.n_min >= thr.min_sample,
        "correlation_ok": theme.mean_corr >= thr.min_correlation,
        "coverage_ok": theme.coverage >= thr.min_cohort_coverage,
    }

    if not checks["sample_ok"]:
        return GuardrailReport(SKIPPED, f"insufficient sample (n_min={theme.n_min} < {thr.min_sample})", checks)
    if not checks["size_ok"]:
        return GuardrailReport(HOLD, f"theme size {len(theme.members)} outside [{thr.min_theme_size},{thr.max_theme_size}]", checks)
    if not checks["correlation_ok"]:
        return GuardrailReport(HOLD, f"mean correlation {theme.mean_corr:.2f} < {thr.min_correlation:.2f}", checks)
    if not checks["coverage_ok"]:
        return GuardrailReport(HOLD, f"cohort coverage {theme.coverage:.0%} < {thr.min_cohort_coverage:.0%}", checks)

    return GuardrailReport(
        PROPOSE,
        f"{len(theme.members)} metrics co-move (mean r={theme.mean_corr:.2f}) across "
        f"{theme.coverage:.0%} of cohorts (n>={theme.n_min})",
        checks,
    )
