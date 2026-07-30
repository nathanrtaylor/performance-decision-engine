"""
Constants and tunable thresholds for benchmark recalculation.

Every guardrail knob lives in ``RecalcThresholds`` so governance can tune the bar without touching
logic. Category membership (which recompute recipe applies) is driven by the metric_catalog category
plus the explicit sets below, matching the hand-curated conventions documented in benchmarks.yaml.
"""
from __future__ import annotations

from dataclasses import dataclass

# 8-week decision window (matches cde.temporal.aggregate window_weeks default).
WINDOW_WEEKS = 8

# p25 floor cap for near-universal behaviors (quality + sentiment): flag only clear misses.
QUALITY_CAP = 0.95

# Scorecard whose behaviors are "sentiment" (Verizon-only). Others on behavior_scores are "quality".
SENTIMENT_SCORECARD = "Customer Sentiment Scorecard V1"

# Operational business metrics: benchmark = per-cohort MEDIAN of agents' windowed means.
OPERATIONAL_METRICS = (
    "transfer_rate",
    "crt",
    "talk_time",
    "hold_time",
    "callback_rate",
    "one_call_resolution",
    "resolution_rate",
)

# Sales: cohort median where a real (non-floor) distribution exists; otherwise absolute stretch target.
SALES_METRICS = ("nsp100",)

# Cohort medians degenerate (floor 0 / ceiling): keep the curated absolute target, do not chase data.
ABSOLUTE_DEFAULT_METRICS = ("cancel_rate", "erp", "expert_5star")

# Tool-usage sources are inactive (no data) -> always skipped, current kept.
TOOL_USAGE_METRICS = ("guided_flow_adoption", "expert_assist_usage", "smart_offer_adoption")

# Behaviors carried as benchmark.type: distribution (opportunity-gated) -> excluded from recompute.
DISTRIBUTION_BEHAVIORS = frozenset(
    {"enroll_with_consent", "provide_self_service_options", "read_t_and_c_s"}
)

# The four cohorts (lowercase canonical form used everywhere in benchmarks.yaml).
COHORTS = ("mob-at&t", "mob-verizon", "pss-at&t", "pss-verizon")

# Verdicts.
PROPOSE = "PROPOSE"
HOLD = "HOLD"
UNCHANGED = "UNCHANGED"
SKIPPED = "SKIPPED"

# Category labels (drive dashboard section grouping).
CAT_OPERATIONAL = "operational"
CAT_SALES = "sales"
CAT_ABSOLUTE = "absolute-default"
CAT_QUALITY = "quality"
CAT_SENTIMENT = "sentiment"
CAT_TOOL = "tool-usage"


@dataclass(frozen=True)
class RecalcThresholds:
    """All guardrail knobs. Defaults chosen to reproduce the current hand-curated benchmarks."""

    # --- Guardrail 1: sample sufficiency (enough evidence to trust the anchor) ---
    min_agents_cohort: int = 15      # emit a by_icp_client value only when the cohort clears this
    min_agents_overall: int = 30     # emit a default only when the overall population clears this

    # --- Guardrail 2: materiality (the move is big enough to bother proposing) ---
    op_rel_change: float = 0.10          # operational/sales: >=10% relative change -> material
    behavior_abs_delta: float = 0.03     # behaviors (0-1 pass-rates): >=0.03 absolute -> material

    # --- Guardrail 3: non-degeneracy (anchor not stuck at a scale boundary) ---
    floor_eps: float = 1e-6              # cohort median at/near 0 -> floor-degenerate
    near_universal: float = QUALITY_CAP  # behavior p25 >= this -> near-universal (cap, no churn)
    erp_ceiling: float = 100.0
    star_ceiling: float = 5.0

    # --- Guardrail 4: cohort-split validity (only split when cohorts genuinely differ) ---
    split_abs: float = 0.05              # |mob-vzw - pss-vzw| >= 0.05 ...
    split_rel: float = 0.15              # ... OR >= 15% relative -> emit by_icp_client

    # --- Guardrail 5: sanity/outlier (proposed value within observed [p_lo, p_hi]) ---
    outlier_lo_q: float = 0.01
    outlier_hi_q: float = 0.99

    # Shared cap for behavior p25 anchors.
    quality_cap: float = QUALITY_CAP
