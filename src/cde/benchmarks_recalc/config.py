"""
Constants and tunable thresholds for benchmark recalculation.

Every guardrail knob lives in ``RecalcThresholds`` so governance can tune the bar without touching
logic. Category membership (which recompute recipe applies) is driven by the metric_catalog category
plus the explicit sets below, matching the hand-curated conventions documented in benchmarks.yaml.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from cde.constants import WINDOW_WEEKS  # single source; re-exported for callers importing from here

# p25 floor cap for near-universal behaviors (quality + sentiment): flag only clear misses.
QUALITY_CAP = 0.95

# Scorecard whose behaviors are "sentiment" (Verizon-only). Kept for test fixtures + prep's dominant-
# scorecard tagging; recipe dispatch is now DECLARATIVE (metric_catalog recalc.recipe), not scorecard-based.
SENTIMENT_SCORECARD = "Customer Sentiment Scorecard V1"

# Default icp_client roster (lowercase canonical form used everywhere in benchmarks.yaml).
# This is only the FALLBACK: the governed roster lives in configs/active.yaml
# (icp_clients:) and reaches recompute via RecalcThresholds.cohorts. Keep this in
# sync as a safety net for callers that build thresholds without a config.
COHORTS = ("mob-at&t", "mob-verizon", "pss-at&t", "pss-at&t nac", "pss-verizon", "mcafee", "xbox")

# Verdicts.
PROPOSE = "PROPOSE"
HOLD = "HOLD"
UNCHANGED = "UNCHANGED"
SKIPPED = "SKIPPED"

# Category labels (drive dashboard section grouping).
CAT_OPERATIONAL = "operational"
CAT_SELL = "sell"
CAT_SERVE = "serve"
CAT_SOLVE = "solve"
CAT_ABSOLUTE = "absolute-default"
CAT_QUALITY = "quality"
CAT_SENTIMENT = "sentiment"
CAT_TOOL = "tool-usage"

# Declarative recalc recipes. A metric's recalc.recipe (metric_catalog.yaml, or a category_defaults
# fallback) selects BOTH the recompute worker (see recompute.RECIPE_WORKERS) and the dashboard section
# below. "skip" produces no candidate. This map is the single source of truth for a candidate's section,
# replacing the per-worker hardcoded category and the old metric-name dispatch tuples.
# sell/serve/solve are the business metric types; sell reuses the nsp100 computation, serve+solve the
# per-cohort operational medians. "operational" is retained as the fallback recipe for the (now unused)
# business category.
RECIPE_SECTION = {
    "operational": CAT_OPERATIONAL,
    "sell": CAT_SELL,
    "serve": CAT_SERVE,
    "solve": CAT_SOLVE,
    "absolute": CAT_ABSOLUTE,
    "quality": CAT_QUALITY,
    "sentiment": CAT_SENTIMENT,
    "tool": CAT_TOOL,
}
VALID_RECIPES = frozenset(RECIPE_SECTION) | {"skip"}


@dataclass(frozen=True)
class RecalcThresholds:
    """All guardrail knobs. Defaults chosen to reproduce the current hand-curated benchmarks."""

    # --- Guardrail 1: sample sufficiency (enough evidence to trust the anchor) ---
    min_agents_cohort: int = 15      # emit a by_icp_client value only when the cohort clears this
    min_agents_overall: int = 30     # emit a default only when the overall population clears this

    # --- Guardrail 2: materiality (the move is big enough to bother proposing) ---
    op_rel_change: float = 0.10          # operational/sell/serve/solve: >=10% relative change -> material
    behavior_abs_delta: float = 0.03     # behaviors (0-1 pass-rates): >=0.03 absolute -> material

    # --- Guardrail 3: non-degeneracy (anchor not stuck at a scale boundary) ---
    # Scale ceilings for absolute metrics now live in metric_catalog (recalc.bound.at), not here, so a
    # metric's scale is declared next to the metric. Only the shared 0-floor epsilon remains global.
    floor_eps: float = 1e-6              # cohort median at/near 0 -> floor-degenerate
    near_universal: float = QUALITY_CAP  # behavior p25 >= this -> near-universal (cap, no churn)

    # --- Guardrail 4: cohort-split validity (only split when cohorts genuinely differ) ---
    split_abs: float = 0.05              # |mob-vzw - pss-vzw| >= 0.05 ...
    split_rel: float = 0.15              # ... OR >= 15% relative -> emit by_icp_client

    # --- Guardrail 5: sanity/outlier (proposed value within observed [p_lo, p_hi]) ---
    outlier_lo_q: float = 0.01
    outlier_hi_q: float = 0.99

    # Shared cap for behavior p25 anchors.
    quality_cap: float = QUALITY_CAP

    # Governed icp_client roster (which per-cohort benchmarks to compute). Sourced
    # from config["icp_clients"] via from_config; defaults to the module fallback.
    cohorts: Tuple[str, ...] = COHORTS

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]] = None) -> "RecalcThresholds":
        """Build from active.yaml: the optional ``benchmark_recalc`` block (guardrail
        knobs) plus the top-level ``icp_clients`` roster.

        Only recognized fields override the curated defaults; an absent/empty block
        and a missing roster reproduce ``RecalcThresholds()`` exactly (so today's
        proposals are unchanged). This is what the module docstring means by
        "governance can tune the bar without touching logic."
        """
        config = config or {}
        block = config.get("benchmark_recalc") or {}
        known = {f.name for f in dataclasses.fields(cls)}
        overrides = {k: v for k, v in block.items() if k in known}
        # Governed roster lives at the top level of active.yaml (icp_clients:),
        # not inside benchmark_recalc; fall back to the module default when absent.
        roster = config.get("icp_clients")
        if roster:
            overrides["cohorts"] = tuple(str(c) for c in roster)
        return dataclasses.replace(cls(), **overrides)
