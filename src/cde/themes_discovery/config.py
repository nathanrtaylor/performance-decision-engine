"""
Constants and tunable thresholds for theme discovery.

Every guardrail knob lives in ``DiscoveryThresholds`` so governance can tune the bar without
touching logic. Discovery is propose-only: these thresholds decide which candidate themes are
surfaced as PROPOSE vs HOLD, but a human SME still makes the final call in themes.yaml.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Dict, Optional

# 8-week decision window (matches cde.temporal.aggregate window_weeks default).
WINDOW_WEEKS = 8

# Verdicts.
PROPOSE = "PROPOSE"
HOLD = "HOLD"
SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class DiscoveryThresholds:
    """All guardrail knobs for co-movement theme discovery."""

    # --- Guardrail 1: sample sufficiency ---
    # Minimum agents with BOTH metrics present in a cohort to trust a pairwise correlation.
    min_sample: int = 25

    # --- Guardrail 2: co-movement strength ---
    # Two metrics "move together" when their direction-adjusted (bad-axis) correlation across
    # agents in a cohort is at least this. Because both metrics are put on a common "higher = worse"
    # axis first, a low-is-better and a high-is-better metric that reflect the same underlying
    # problem show up as POSITIVE correlation here.
    min_correlation: float = 0.40

    # --- Guardrail 3: cohort coverage ---
    # A metric pair must clear min_correlation in at least this fraction of the cohorts where both
    # metrics had sufficient sample (so a theme reflects a stable pattern, not one cohort's quirk).
    min_cohort_coverage: float = 0.50

    # --- Guardrail 4: theme size sanity ---
    min_theme_size: int = 2
    max_theme_size: int = 6

    window_weeks: int = WINDOW_WEEKS

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]] = None) -> "DiscoveryThresholds":
        """Build from the optional ``theme_discovery`` block in active.yaml.

        Only recognized fields override defaults; an absent/empty block reproduces
        ``DiscoveryThresholds()`` exactly (so today's proposals are unchanged).
        """
        block = (config or {}).get("theme_discovery") or {}
        known = {f.name for f in dataclasses.fields(cls)}
        overrides = {k: v for k, v in block.items() if k in known}
        return dataclasses.replace(cls(), **overrides)
