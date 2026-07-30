# src/cde/scoring/assemble.py
"""
Single, direction-aware, deterministic multi-axis scoring.

This module is the ONE place that turns performance signals into the four score axes
(level / trend / risk / confidence) and composes them into ``score_total`` using the
versioned ``priority_model`` weights. It is used for both:

  - the primary decision grain: the 8-week windowed frame  (compute_windowed_scores)
  - optional per-period diagnostics                          (assemble_scores)

Semantics (business-aligned + governed):
  * Only *underperformance* scores. Direction (from ``metric_catalog``) decides which way is
    bad; good performers score ~0, so we never recommend coaching a strength.
  * Scores are *percentile-based, not absolute*: score_level is the agent's percentile minus the
    benchmark's percentile within the peer distribution for that metric. This makes metrics on
    wildly different scales (e.g. crt ~1400 vs transfer_rate ~0.12) directly comparable and keeps
    a unit-mismatched benchmark from letting one metric dominate (every score is bounded 0..1).
  * risk = level x (1 - confidence): a big, certain gap is urgent.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from cde.utils.logging import get_logger

log = get_logger(__name__)

_KEYS = ["agent_id", "period", "call_type", "metric"]
_SCORE_COLS = ["score_level", "score_trend", "score_risk", "score_confidence", "score_total"]


def _unwrap_root(obj: Any, root_key: str) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        return {}
    inner = obj.get(root_key)
    return inner if isinstance(inner, dict) else obj


def _priority_model(config: Dict[str, Any]) -> tuple[float, float, float]:
    pm = config.get("priority_model") or {}
    return (
        float(pm.get("w_level", 0.5)),
        float(pm.get("w_trend", 0.2)),
        float(pm.get("w_risk", 0.3)),
    )


def _metric_directions(config: Dict[str, Any]) -> Dict[str, str]:
    mc = _unwrap_root(config.get("metric_catalog") or {}, "metric_catalog")
    metrics = mc.get("metrics") or {}
    return {m: (meta.get("direction") or "higher_is_better") for m, meta in metrics.items()}


def _pct_beyond_boundary(bad: pd.Series, group: pd.Series, boundary: float = 0.0) -> pd.Series:
    """
    Percentile-difference score, unit- and scale-free.

    ``bad`` is a direction-adjusted quantity where higher = worse (a deficit vs benchmark, or a
    worsening trend). Within each peer ``group`` (e.g. all agents for one metric), we compute:

        score = agent_percentile - benchmark_percentile     (clipped at 0)

    where percentile is the empirical CDF across peers and the benchmark sits at ``bad == boundary``
    (0). This measures how much further into the *bad tail* an agent is than the benchmark standing,
    expressed in percentile terms. It is identical across metrics regardless of absolute magnitude,
    so a metric with a large scalar range (e.g. CRT ~1400) cannot dominate one near ~0.1, and a
    unit-mismatched benchmark can shift the boundary but never blow the score past 1.0.
    """
    frame = pd.DataFrame({"bad": pd.to_numeric(bad, errors="coerce")})
    frame["_g"] = list(group)
    g = frame.groupby("_g", dropna=False)["bad"]
    agent_cdf = g.rank(pct=True, method="average")
    boundary_cdf = g.transform(lambda s: (s <= boundary).mean())
    score = (agent_cdf - boundary_cdf).clip(lower=0.0)
    return pd.to_numeric(score, errors="coerce").fillna(0.0)


def _compute_scores(
    df: pd.DataFrame,
    config: Dict[str, Any],
    *,
    gap_col: str,
    trend_col: str,
    confidence_col: str,
    pop_group_cols: list[str],
    direction_col: Optional[str] = None,
) -> pd.DataFrame:
    """
    Add score_level/score_trend/score_risk/score_confidence/score_total to ``df``.

    Scoring is *percentile-based*, not absolute (see _pct_beyond_boundary):
      - score_level: agent percentile minus benchmark percentile within the peer population
        (``pop_group_cols``), using the signed ``gap_col`` (value - benchmark) direction-adjusted.
      - score_trend: same idea on the worsening trend (boundary = no change).
    ``gap_col`` is a signed gap; ``trend_col`` a signed change in that gap (>0 means gap growing).
    Direction comes from ``direction_col`` when present, else the metric_catalog.
    """
    out = df.copy()

    gap = pd.to_numeric(out.get(gap_col), errors="coerce")
    trend = pd.to_numeric(out.get(trend_col), errors="coerce")
    conf = pd.to_numeric(out.get(confidence_col), errors="coerce").clip(0.0, 1.0).fillna(0.0)

    # Direction per row: lower_is_better means a higher value (positive gap) is bad.
    if direction_col and direction_col in out.columns:
        direction = out[direction_col].astype(str)
    else:
        dirs = _metric_directions(config)
        direction = out["metric"].map(lambda m: dirs.get(m, "higher_is_better"))
    lower_is_better = direction.eq("lower_is_better")

    # Direction-adjust so that, in every case, a larger value means "worse".
    bad_gap = gap.where(lower_is_better, -gap)            # deficit vs benchmark (>0 = worse)
    worsening = trend.where(lower_is_better, -trend)      # trend getting worse (>0 = worsening)

    # Peer population for percentile ranking (e.g. all agents for a metric in the window).
    group = out[pop_group_cols].astype(str).agg("|".join, axis=1)

    out["score_level"] = _pct_beyond_boundary(bad_gap, group, boundary=0.0).values
    out["score_trend"] = _pct_beyond_boundary(worsening, group, boundary=0.0).values
    out["score_confidence"] = conf.values
    out["score_risk"] = (out["score_level"] * (1.0 - out["score_confidence"])).fillna(0.0)

    w_level, w_trend, w_risk = _priority_model(config)
    out["score_total"] = (
        w_level * out["score_level"]
        + w_trend * out["score_trend"]
        + w_risk * out["score_risk"]
    ).astype(float)

    return out


def _warn_benchmark_outliers(df: pd.DataFrame, config: Dict[str, Any], lo_q: float = 0.01, hi_q: float = 0.99) -> None:
    """
    Data-quality guard for wrong-scale / wrong-unit benchmarks: flag a metric when its benchmark
    falls OUTSIDE the observed value range [p1, p99]. This catches genuine scale mismatches
    (e.g. talk_time=50 vs a 700-1800s distribution, nsp100=1.0 vs 0-0.17) without flagging a
    benchmark that is deliberately strict but on the right scale (e.g. a 0.95 floor on a 0-1
    behavior). A metric whose benchmark sits below its entire distribution (no discrimination)
    still surfaces here, which is the intended signal.

    Windowed value is reconstructed as level_8w (mean gap) + benchmark_8w (mean benchmark).
    """
    if not {"level_8w", "benchmark_8w"}.issubset(df.columns) or "metric" not in df.columns:
        return
    for metric, g in df.groupby("metric"):
        lvl = pd.to_numeric(g["level_8w"], errors="coerce")
        bench = pd.to_numeric(g["benchmark_8w"], errors="coerce")
        value = (lvl + bench).dropna()
        b = bench.dropna()
        if value.empty or b.empty:
            continue
        bval = float(b.median())
        lo, hi = float(value.quantile(lo_q)), float(value.quantile(hi_q))
        if bval < lo or bval > hi:
            log.warning(
                "benchmark check: metric '%s' benchmark (%.4g) is outside the observed value "
                "range [%.4g, %.4g] - likely a wrong scale/unit; review configs/mappings/benchmarks.yaml.",
                metric, bval, lo, hi,
            )


def compute_windowed_scores(windowed: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """
    Primary path: score the 8-week windowed frame produced by
    ``temporal.aggregate.aggregate_scores_window``.

    Input columns (min): agent_id, call_type, metric, window_end, level_8w (mean signed gap),
      trend_8w (slope of gap), confidence_8w (coverage), benchmark_8w, direction.
    Output: keys + score_* columns; ``period`` is set to ``window_end`` (the decision grain),
      plus a few carried diagnostics.
    """
    if windowed is None or windowed.empty:
        return pd.DataFrame(columns=_KEYS + _SCORE_COLS)

    df = windowed.copy()
    if "period" not in df.columns:
        df["period"] = df.get("window_end")

    _warn_benchmark_outliers(df, config)

    scored = _compute_scores(
        df,
        config,
        gap_col="level_8w",
        trend_col="trend_8w",
        confidence_col="confidence_8w",
        pop_group_cols=["metric", "call_type"],  # peers = all agents for a metric in the window
        direction_col="direction" if "direction" in df.columns else None,
    )

    diagnostics = [
        c
        for c in ["window_start", "window_end", "weeks_present", "denom_8w", "level_8w", "trend_8w",
                  "volatility_8w", "benchmark_8w", "direction", "recency_shift"]
        if c in scored.columns
    ]
    return scored[_KEYS + _SCORE_COLS + diagnostics].copy()


def assemble_scores(signals: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """
    Per-period diagnostic scoring (used for the optional scores.csv). Same semantics as the
    windowed path, applied to weekly signals. One row per (agent_id, period, call_type, metric).
    """
    if signals is None or signals.empty:
        return pd.DataFrame(columns=_KEYS + _SCORE_COLS)

    scored = _compute_scores(
        signals,
        config,
        gap_col="gap",
        trend_col="trend",
        confidence_col="confidence",
        pop_group_cols=["period", "metric", "call_type"],  # peers = all agents for a metric that week
        direction_col="direction" if "direction" in signals.columns else None,
    )

    dup = int(scored.duplicated(subset=_KEYS, keep=False).sum())
    if dup:
        log.warning("assemble_scores: %s duplicate rows on %s; keeping max(score_total).", dup, _KEYS)
        scored = (
            scored.sort_values("score_total", ascending=False)
            .drop_duplicates(subset=_KEYS, keep="first")
            .reset_index(drop=True)
        )

    return scored[_KEYS + _SCORE_COLS].copy()
