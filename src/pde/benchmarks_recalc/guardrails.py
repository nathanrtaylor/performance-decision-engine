"""
Guardrail evaluation: decide PROPOSE / HOLD / UNCHANGED per (metric, cohort).

A change is PROPOSED only when the evidence clears every applicable guardrail. Distinctions:
  HOLD       -> evidence insufficient / degenerate / out-of-range: do NOT change the benchmark.
  UNCHANGED  -> guardrails pass but the move isn't material (avoid churn); keep current value.
  PROPOSE    -> guardrails pass AND the move is material (or the cohort/metric is new).

The evidence for each check is retained so the dashboard can justify the verdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from . import config as C
from .config import RecalcThresholds
from .recompute import CohortStat


@dataclass(frozen=True)
class GuardrailReport:
    verdict: str                     # PROPOSE | HOLD | UNCHANGED
    reason: str                      # human-readable driver of the verdict
    checks: Dict[str, bool] = field(default_factory=dict)


def _is_material(new: float, old: Optional[float], category: str, thr: RecalcThresholds) -> bool:
    if old is None:
        return True
    behaviorish = category in (C.CAT_QUALITY, C.CAT_SENTIMENT)
    if behaviorish:
        return abs(new - old) >= thr.behavior_abs_delta
    base = abs(old) if abs(old) > 1e-9 else 1e-9
    return abs(new - old) / base >= thr.op_rel_change


def evaluate(
    stat: CohortStat,
    current_value: Optional[float],
    category: str,
    value_lo: Optional[float],
    value_hi: Optional[float],
    thr: RecalcThresholds,
) -> GuardrailReport:
    checks: Dict[str, bool] = {}

    # Guardrail 3 (non-degeneracy) + "nothing to propose"
    checks["non_degenerate"] = not stat.degenerate
    if stat.value is None or stat.degenerate:
        return GuardrailReport(C.HOLD, stat.note or "degenerate / no proposable value", checks)

    # Guardrail 1 (sample sufficiency)
    checks["sample_sufficient"] = stat.sufficient
    if not stat.sufficient:
        return GuardrailReport(C.HOLD, stat.note, checks)

    # Guardrail 2 (materiality). An immaterial move (proposed ~= current) is UNCHANGED: there is
    # nothing to apply, so the outlier sanity check below (which only matters when actually changing
    # a value) is intentionally skipped -- otherwise a deliberate floor equal to the current value
    # would false-alarm as "out of range".
    material = _is_material(stat.value, current_value, category, thr)
    checks["material_change"] = material
    if current_value is not None and not material:
        return GuardrailReport(C.UNCHANGED, f"{stat.note}; within materiality threshold of current", checks)

    # Guardrail 5 (sanity / outlier): a material/new value must sit within observed [p_lo, p_hi]
    in_range = True
    if value_lo is not None and value_hi is not None:
        in_range = value_lo <= stat.value <= value_hi
    checks["within_observed_range"] = in_range
    if not in_range:
        return GuardrailReport(
            C.HOLD,
            f"proposed {stat.value:.4g} outside observed range "
            f"[{value_lo:.4g}, {value_hi:.4g}] - review before applying",
            checks,
        )

    if current_value is None:
        return GuardrailReport(C.PROPOSE, f"{stat.note}; new benchmark (no current value)", checks)
    delta = stat.value - current_value
    return GuardrailReport(C.PROPOSE, f"{stat.note}; Δ{delta:+.3g} vs current {current_value:.3g}", checks)
