"""
Per-category benchmark candidate computation.

Each metric yields a ``CandidateBenchmark`` carrying the proposed value(s) AND the evidence that
justifies them (sample size, quantile used, degeneracy/cap/split flags). Guardrail evaluation and
old-vs-new comparison happen downstream; this module only computes candidates + evidence.

Recipe per category (see benchmarks.yaml methodology comments):
  operational -> per-cohort MEDIAN of agents' windowed means
  sales(nsp100) -> cohort median where a real distribution exists; floor -> keep absolute default
  absolute-default -> keep curated target when cohort medians are degenerate (floor/ceiling)
  quality behaviors -> p25 of windowed means, capped 0.95, no cohort split
  sentiment behaviors -> p25 capped, Verizon-only cohorts, split only when cohorts differ materially
  tool-usage -> skipped (sources inactive)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import pandas as pd

from . import config as C
from .config import RecalcThresholds
from .prep import PreppedFrames, windowed_mean_per_agent


@dataclass(frozen=True)
class CohortStat:
    cohort: str                      # 'default' or an icp_client
    value: Optional[float]           # proposed benchmark (post cap/degeneracy); None -> keep current
    n_agents: int
    quantile_used: str               # 'median' | 'p25' | 'absolute'
    raw_quantile: Optional[float]    # pre-cap/pre-degeneracy data anchor
    sufficient: bool
    degenerate: bool
    capped: bool
    note: str


@dataclass(frozen=True)
class CandidateBenchmark:
    metric: str
    category: str
    default: CohortStat
    by_icp_client: Dict[str, CohortStat] = field(default_factory=dict)
    split_applied: bool = False
    skipped: bool = False
    value_lo: Optional[float] = None   # observed [p_lo, p_hi] of windowed means (outlier guardrail)
    value_hi: Optional[float] = None


# ---------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------

def _series_for_cohort(wm: pd.DataFrame, cohort: Optional[str]) -> pd.Series:
    if wm is None or wm.empty:
        return pd.Series(dtype="float64")
    if cohort is None:
        return pd.to_numeric(wm["mean_calc"], errors="coerce").dropna()
    if "icp_client" not in wm.columns:
        return pd.Series(dtype="float64")
    sub = wm[wm["icp_client"].astype(str).str.strip().str.lower() == cohort]
    return pd.to_numeric(sub["mean_calc"], errors="coerce").dropna()


def _value_range(wm: pd.DataFrame, thr: RecalcThresholds) -> Tuple[Optional[float], Optional[float]]:
    s = _series_for_cohort(wm, None)
    if s.empty:
        return None, None
    return float(s.quantile(thr.outlier_lo_q)), float(s.quantile(thr.outlier_hi_q))


def _median_stat(wm: pd.DataFrame, cohort: Optional[str], thr: RecalcThresholds) -> CohortStat:
    s = _series_for_cohort(wm, cohort)
    n = int(s.shape[0])
    label = cohort or "default"
    min_n = thr.min_agents_cohort if cohort else thr.min_agents_overall
    if n == 0:
        return CohortStat(label, None, 0, "median", None, False, False, False, "no agents in cohort")
    raw = float(s.median())
    sufficient = n >= min_n
    note = f"median of {n} agents" + ("" if sufficient else f" (< {min_n}: insufficient)")
    return CohortStat(label, raw, n, "median", raw, sufficient, False, False, note)


def _p25_stat(wm: pd.DataFrame, cohort: Optional[str], thr: RecalcThresholds) -> CohortStat:
    s = _series_for_cohort(wm, cohort)
    n = int(s.shape[0])
    label = cohort or "default"
    min_n = thr.min_agents_cohort if cohort else thr.min_agents_overall
    if n == 0:
        return CohortStat(label, None, 0, "p25", None, False, False, False, "no agents in cohort")
    raw = float(s.quantile(0.25))
    capped = raw >= thr.quality_cap
    value = round(thr.quality_cap, 3) if capped else round(raw, 3)
    sufficient = n >= min_n
    note = (
        f"p25={raw:.3f} of {n} agents"
        + (f" capped at {thr.quality_cap}" if capped else "")
        + ("" if sufficient else f" (< {min_n}: insufficient)")
    )
    return CohortStat(label, value, n, "p25", raw, sufficient, degenerate=False, capped=capped, note=note)


# ---------------------------------------------------------------------------------------------------
# category workers
# ---------------------------------------------------------------------------------------------------

def recompute_operational(prepped: PreppedFrames, metric: str, thr: RecalcThresholds) -> CandidateBenchmark:
    dmin = prepped.metric_meta[metric].denominator_min
    wm = windowed_mean_per_agent(prepped.agent_metrics, metric, cohort_col="icp_client", denominator_min=dmin)
    lo, hi = _value_range(wm, thr)
    default = _median_stat(wm, None, thr)
    by_cohort = {c: _median_stat(wm, c, thr) for c in C.COHORTS}
    by_cohort = {c: s for c, s in by_cohort.items() if s.n_agents > 0}
    return CandidateBenchmark(metric, C.CAT_OPERATIONAL, default, by_cohort, bool(by_cohort), False, lo, hi)


def recompute_nsp100(prepped: PreppedFrames, metric: str, thr: RecalcThresholds) -> CandidateBenchmark:
    dmin = prepped.metric_meta[metric].denominator_min
    wm = windowed_mean_per_agent(prepped.agent_metrics, metric, cohort_col="icp_client", denominator_min=dmin)
    lo, hi = _value_range(wm, thr)
    default = _median_stat(wm, None, thr)
    by_cohort: Dict[str, CohortStat] = {}
    for c in C.COHORTS:
        st = _median_stat(wm, c, thr)
        if st.n_agents == 0:
            continue
        if st.raw_quantile is not None and st.raw_quantile <= thr.floor_eps:
            # cohort distribution sits at the 0 floor -> not a usable relative bar
            by_cohort[c] = CohortStat(c, None, st.n_agents, "median", st.raw_quantile, st.sufficient,
                                      True, False, f"cohort median at floor 0 ({st.n_agents} agents)")
        else:
            by_cohort[c] = st
    return CandidateBenchmark(metric, C.CAT_SALES, default, by_cohort, bool(by_cohort), False, lo, hi)


def recompute_absolute(prepped: PreppedFrames, metric: str, thr: RecalcThresholds) -> CandidateBenchmark:
    dmin = prepped.metric_meta[metric].denominator_min
    wm = windowed_mean_per_agent(prepped.agent_metrics, metric, cohort_col=None, denominator_min=dmin)
    lo, hi = _value_range(wm, thr)
    s = _series_for_cohort(wm, None)
    n = int(s.shape[0])
    if n == 0:
        default = CohortStat("default", None, 0, "absolute", None, False, True, False, "no data")
        return CandidateBenchmark(metric, C.CAT_ABSOLUTE, default, {}, split_applied=False,
                                  skipped=False, value_lo=lo, value_hi=hi)
    raw = float(s.median())
    if metric == "cancel_rate":
        degenerate = raw <= thr.floor_eps
        kind = "floor 0"
    elif metric == "erp":
        degenerate = raw >= thr.erp_ceiling
        kind = f"ceiling {thr.erp_ceiling:g}"
    elif metric == "expert_5star":
        degenerate = raw >= thr.star_ceiling
        kind = f"ceiling {thr.star_ceiling:g}"
    else:
        degenerate = False
        kind = ""
    if degenerate:
        note = f"cohort median degenerate at {kind} -> keep curated absolute target"
        default = CohortStat("default", None, n, "absolute", raw, n >= thr.min_agents_overall, True, False, note)
    else:
        note = f"median of {n} agents (non-degenerate)"
        default = CohortStat("default", round(raw, 3), n, "median", raw, n >= thr.min_agents_overall, False, False, note)
    return CandidateBenchmark(metric, C.CAT_ABSOLUTE, default, {}, split_applied=False,
                              skipped=False, value_lo=lo, value_hi=hi)


def recompute_quality(prepped: PreppedFrames, metric: str, thr: RecalcThresholds) -> CandidateBenchmark:
    wm = windowed_mean_per_agent(prepped.behavior_scores, metric, cohort_col=None, denominator_min=None)
    lo, hi = _value_range(wm, thr)
    default = _p25_stat(wm, None, thr)
    return CandidateBenchmark(metric, C.CAT_QUALITY, default, {}, split_applied=False,
                              skipped=False, value_lo=lo, value_hi=hi)


def recompute_sentiment(prepped: PreppedFrames, metric: str, thr: RecalcThresholds) -> CandidateBenchmark:
    wm = windowed_mean_per_agent(prepped.behavior_scores, metric, cohort_col="icp_client", denominator_min=None)
    lo, hi = _value_range(wm, thr)
    default = _p25_stat(wm, None, thr)

    # Verizon-only scope: candidate cohorts are the two Verizon cohorts.
    vz = ["mob-verizon", "pss-verizon"]
    cohort_stats = {c: _p25_stat(wm, c, thr) for c in vz}
    cohort_stats = {c: s for c, s in cohort_stats.items() if s.n_agents > 0}

    split_applied = False
    by_icp: Dict[str, CohortStat] = {}
    if len(cohort_stats) == 2 and all(s.sufficient for s in cohort_stats.values()):
        mob, pss = cohort_stats["mob-verizon"], cohort_stats["pss-verizon"]
        if mob.value is not None and pss.value is not None:
            delta = abs(mob.value - pss.value)
            base = max(abs(mob.value), abs(pss.value), 1e-9)
            if delta >= thr.split_abs or (delta / base) >= thr.split_rel:
                split_applied = True
                reason = f"mob {mob.value:.3f} vs pss {pss.value:.3f} Δ{delta:.3f} >= split threshold"
                by_icp = {
                    "mob-verizon": _with_note(mob, reason),
                    "pss-verizon": _with_note(pss, reason),
                }
    return CandidateBenchmark(metric, C.CAT_SENTIMENT, default, by_icp, split_applied, False, lo, hi)


def _with_note(st: CohortStat, extra: str) -> CohortStat:
    return CohortStat(st.cohort, st.value, st.n_agents, st.quantile_used, st.raw_quantile,
                      st.sufficient, st.degenerate, st.capped, f"{st.note}; {extra}")


def recompute_tool(metric: str) -> CandidateBenchmark:
    default = CohortStat("default", None, 0, "absolute", None, False, False, False,
                         "source inactive (no data) -> keep current")
    return CandidateBenchmark(metric, C.CAT_TOOL, default, {}, False, True)


# ---------------------------------------------------------------------------------------------------
# router
# ---------------------------------------------------------------------------------------------------

def recompute_all(prepped: PreppedFrames, thr: RecalcThresholds) -> Dict[str, CandidateBenchmark]:
    out: Dict[str, CandidateBenchmark] = {}
    for metric, meta in prepped.metric_meta.items():
        if metric in C.TOOL_USAGE_METRICS:
            out[metric] = recompute_tool(metric)
        elif metric in C.ABSOLUTE_DEFAULT_METRICS:
            out[metric] = recompute_absolute(prepped, metric, thr)
        elif metric in C.SALES_METRICS:
            out[metric] = recompute_nsp100(prepped, metric, thr)
        elif metric in C.OPERATIONAL_METRICS:
            out[metric] = recompute_operational(prepped, metric, thr)
        elif meta.category == "quality_behavior":
            # exclude distribution-type / non-config behaviors
            if meta.benchmark_type != "config" or metric in C.DISTRIBUTION_BEHAVIORS:
                continue
            scorecard = prepped.behavior_scorecards.get(metric, "")
            if scorecard == C.SENTIMENT_SCORECARD:
                out[metric] = recompute_sentiment(prepped, metric, thr)
            else:
                out[metric] = recompute_quality(prepped, metric, thr)
        # else: unknown metric -> no candidate
    return out
