# src/cde/temporal/aggregate.py
from __future__ import annotations

from typing import Dict, Any, List
import numpy as np
import pandas as pd

from cde.utils.logging import get_logger

log = get_logger(__name__)


DEFAULT_TEMPORAL_CONFIG: Dict[str, Any] = {
    # Windowing
    "window_weeks": 8,
    "period_col": "period",
    "value_col": "value",       # used for trend if gap not present / not used
    "gap_col": "gap",           # recommended for level/trend if your gap is “badness vs benchmark”
    "use_gap_for_level": True,
    "use_gap_for_trend": True,

    # Trend method
    "trend_method": "slope",    # "slope" or "last_minus_first"
    "min_weeks_for_trend": 3,   # avoid nonsense slopes on tiny samples

    # Confidence
    "confidence_method": "coverage",  # "coverage" or "coverage_x_stability"
    "stability_dampener": 1.0,        # higher = more dampening when volatility is high (only for coverage_x_stability)

    # Sample-size-aware confidence (evidence DEPTH, not just week coverage):
    # confidence gets a volume_factor = min(1, total_window_denominator / (denom_min * volume_target_weeks)).
    # Per-metric denom_min comes from metric_catalog.computation_override.denominator_min.
    "use_sample_size_confidence": True,
    "volume_target_weeks": 4,          # full volume credit at this many weeks' worth of the metric's denom floor
    "min_window_denominator_default": 10,  # fallback per-metric weekly floor when the catalog has none

    # Window-level minimum total sample: drop thin-evidence windows entirely (a rate on too few
    # observations across the whole window is noise). Floor = denom_min * min_window_weeks.
    "min_window_weeks": 2,             # set to 0 to disable the hard drop (keep only the confidence penalty)

    # Optional recency shift (last 2 vs prior 6)
    "include_recency_shift": True,
    "recent_weeks": 2,          # last N weeks in window considered "recent"

    # If your gap sign convention is opposite, flip here so + means “worse”
    # e.g., if negative gap means worse, set flip_gap_sign=True
    "flip_gap_sign": False,
}


def _metric_denom_min(config: Dict[str, Any], default: float) -> Dict[str, float]:
    """Per-metric weekly denominator floor from metric_catalog (fallback = default)."""
    mc = (config or {}).get("metric_catalog") or {}
    mc = mc.get("metric_catalog", mc) if isinstance(mc, dict) else {}
    metrics = mc.get("metrics") or {}
    out: Dict[str, float] = {}
    for m, meta in metrics.items():
        dm = ((meta or {}).get("computation_override") or {}).get("denominator_min")
        out[m] = float(dm) if dm is not None else float(default)
    return out


def _merge_config(config: Dict[str, Any]) -> Dict[str, Any]:
    temporal = (config or {}).get("temporal", {}) if isinstance(config, dict) else {}
    merged = dict(DEFAULT_TEMPORAL_CONFIG)
    merged.update(temporal or {})
    return merged


def _coerce_datetime(s: pd.Series) -> pd.Series:
    # Period is expected to be week-ending date; we tolerate strings
    return pd.to_datetime(s, errors="coerce")


def _get_last_n_periods(df: pd.DataFrame, period_col: str, n: int) -> List[pd.Timestamp]:
    periods = _coerce_datetime(df[period_col]).dropna().unique()
    if len(periods) == 0:
        return []
    periods = np.sort(periods)  # ascending
    return list(periods[-n:])


def _safe_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _slope_over_time(x: np.ndarray, y: np.ndarray) -> float:
    """
    Compute slope of y ~ x via simple least squares.
    Returns 0.0 if degenerate.
    """
    if len(x) != len(y) or len(x) < 2:
        return 0.0

    x = x.astype(float)
    y = y.astype(float)

    # keep only finite y
    mask = np.isfinite(y) & np.isfinite(x)
    x2 = x[mask]
    y2 = y[mask]

    if len(x2) < 2 or np.nanstd(x2) == 0:
        return 0.0

    # slope = cov(x,y) / var(x)
    vx = np.var(x2)
    if vx == 0:
        return 0.0
    cov = np.mean((x2 - np.mean(x2)) * (y2 - np.mean(y2)))
    return float(cov / vx)


def aggregate_scores_window(
    eligible_signals: pd.DataFrame,
    config: Dict[str, Any],
) -> pd.DataFrame:
    """
    Collapse weekly eligible_signals into one row per agent_id × call_type × metric
    over the most recent N unique periods (default 8).

    Expected eligible_signals columns (minimum):
      - agent_id, period, call_type, metric
      - gap (recommended) and/or value

    Output columns:
      - agent_id, call_type, metric
      - window_start, window_end, weeks_present
      - level_8w, trend_8w, volatility_8w, confidence_8w
      - recency_shift (optional)
    """
    cols_out = [
        "agent_id",
        "call_type",
        "metric",
        "window_start",
        "window_end",
        "weeks_present",
        "level_8w",
        "trend_8w",
        "volatility_8w",
        "confidence_8w",
        "denom_8w",
        "benchmark_8w",
        "direction",
    ]

    # Return empty with expected schema
    if eligible_signals is None or eligible_signals.empty:
        return pd.DataFrame(columns=cols_out + ["recency_shift"])

    cfg = _merge_config(config)

    period_col = cfg["period_col"]
    value_col = cfg["value_col"]
    gap_col = cfg["gap_col"]

    required = {"agent_id", period_col, "call_type", "metric"}
    missing_required = [c for c in required if c not in eligible_signals.columns]
    if missing_required:
        raise ValueError(f"aggregate_scores_window: missing required columns: {missing_required}")

    df = eligible_signals.copy()

    # Coerce period
    df[period_col] = _coerce_datetime(df[period_col])
    df = df[df[period_col].notna()]
    if df.empty:
        return pd.DataFrame(columns=cols_out + ["recency_shift"])

    # Choose which series drives level/trend/volatility
    use_gap_level = bool(cfg.get("use_gap_for_level", True)) and (gap_col in df.columns)
    use_gap_trend = bool(cfg.get("use_gap_for_trend", True)) and (gap_col in df.columns)

    # numeric conversions
    if gap_col in df.columns:
        df[gap_col] = _safe_numeric(df[gap_col])
        if cfg.get("flip_gap_sign", False):
            df[gap_col] = -df[gap_col]

    if value_col in df.columns:
        df[value_col] = _safe_numeric(df[value_col])

    # Window: last N unique periods globally (deterministic “latest completed week” behavior)
    window_weeks = int(cfg.get("window_weeks", 8))
    window_periods = _get_last_n_periods(df, period_col, window_weeks)
    if not window_periods:
        return pd.DataFrame(columns=cols_out + ["recency_shift"])

    df = df[df[period_col].isin(window_periods)].copy()
    if df.empty:
        return pd.DataFrame(columns=cols_out + ["recency_shift"])

    window_start = min(window_periods)
    window_end = max(window_periods)

    # Decide aggregation series
    level_series_name = gap_col if use_gap_level else value_col
    trend_series_name = gap_col if use_gap_trend else value_col

    if level_series_name not in df.columns:
        raise ValueError(
            f"aggregate_scores_window: missing '{level_series_name}' for level computation "
            f"(configure temporal.use_gap_for_level/use_gap_for_trend or ensure columns exist)"
        )
    if trend_series_name not in df.columns:
        raise ValueError(
            f"aggregate_scores_window: missing '{trend_series_name}' for trend computation "
            f"(configure temporal.use_gap_for_level/use_gap_for_trend or ensure columns exist)"
        )

    include_recency = bool(cfg.get("include_recency_shift", True))
    recent_weeks = int(cfg.get("recent_weeks", 2))
    recent_weeks = max(1, min(recent_weeks, window_weeks))

    # Precompute a stable week index within the window
    periods_sorted = sorted(window_periods)
    period_to_idx = {p: i for i, p in enumerate(periods_sorted)}
    df["_week_idx"] = df[period_col].map(period_to_idx).astype(float)

    trend_method = str(cfg.get("trend_method", "slope")).lower()
    min_weeks_for_trend = int(cfg.get("min_weeks_for_trend", 3))

    confidence_method = str(cfg.get("confidence_method", "coverage")).lower()
    stability_dampener = float(cfg.get("stability_dampener", 1.0))

    # Sample-size (evidence-depth) settings
    use_volume = bool(cfg.get("use_sample_size_confidence", True))
    volume_target_weeks = float(cfg.get("volume_target_weeks", 4))
    denom_default = float(cfg.get("min_window_denominator_default", 10))
    denom_min_by_metric = _metric_denom_min(config, denom_default)
    has_denominator = "denominator" in df.columns
    if has_denominator:
        df["denominator"] = _safe_numeric(df["denominator"])

    group_keys = ["agent_id", "call_type", "metric"]

    # ---- Vectorized aggregation (grouped column ops; equivalent to the former per-group apply
    #      but ~100x faster at scale). NaN-safe: only finite level values count. ----
    df["_lvl"] = pd.to_numeric(df[level_series_name], errors="coerce")
    df.loc[~np.isfinite(df["_lvl"].to_numpy(dtype=float, na_value=np.nan)), "_lvl"] = np.nan
    if "benchmark" in df.columns:
        df["_bench"] = pd.to_numeric(df["benchmark"], errors="coerce")

    gb = df.groupby(group_keys, dropna=False, sort=True)

    # level / volatility (population std ddof=0 to match np.std); 0.0 when no finite values
    level_8w = gb["_lvl"].mean()
    volatility_8w = gb["_lvl"].std(ddof=0)
    agg = pd.DataFrame(index=level_8w.index)
    agg["level_8w"] = level_8w.fillna(0.0)
    agg["volatility_8w"] = volatility_8w.fillna(0.0)

    # weeks_present: distinct periods among rows with a finite level value
    wp = df[df["_lvl"].notna()].groupby(group_keys, dropna=False)[period_col].nunique()
    agg["weeks_present"] = wp.reindex(agg.index).fillna(0).astype(int)

    # trend: least-squares slope of the trend series over week index, on finite (x, y) pairs.
    # slope = cov(x,y)/var(x) computed via grouped sums (population moments, matching _slope_over_time)
    x = pd.to_numeric(df["_week_idx"], errors="coerce")
    y = pd.to_numeric(df[trend_series_name], errors="coerce")
    tmask = np.isfinite(x.to_numpy(float, na_value=np.nan)) & np.isfinite(y.to_numpy(float, na_value=np.nan))
    tdf = pd.DataFrame({k: df[k] for k in group_keys})
    tdf["_x"] = x
    tdf["_y"] = y
    tdf["_xx"] = x * x
    tdf["_xy"] = x * y
    tdf = tdf[tmask]
    gt = tdf.groupby(group_keys, dropna=False)
    n = gt.size().astype(float)
    Sx, Sy = gt["_x"].sum(), gt["_y"].sum()
    varx = gt["_xx"].sum() / n - (Sx / n) ** 2
    cov = gt["_xy"].sum() / n - (Sx / n) * (Sy / n)
    if trend_method == "last_minus_first":
        ts = tdf.sort_values(period_col)
        raw_trend = ts.groupby(group_keys, dropna=False)["_y"].last() - ts.groupby(group_keys, dropna=False)["_y"].first()
        trend_valid_var = pd.Series(True, index=raw_trend.index)
    else:
        raw_trend = cov / varx
        trend_valid_var = varx > 0
    n_r = n.reindex(agg.index).fillna(0.0)
    valid = (agg["weeks_present"] >= min_weeks_for_trend) & (n_r >= 2) & trend_valid_var.reindex(agg.index).fillna(False)
    agg["trend_8w"] = np.where(valid.to_numpy(), raw_trend.reindex(agg.index).fillna(0.0).to_numpy(), 0.0)

    # total sample over the window (evidence DEPTH): sum of finite denominators, NaN if none
    if has_denominator:
        agg["denom_8w"] = gb["denominator"].sum(min_count=1).reindex(agg.index)
    else:
        agg["denom_8w"] = np.nan

    # volume factor: saturating credit for total sample vs each metric's floor; 1.0 when no denom
    metrics_idx = agg.index.get_level_values("metric")
    vt = np.array([denom_min_by_metric.get(m, denom_default) for m in metrics_idx], dtype=float) * volume_target_weeks
    d8 = pd.to_numeric(agg["denom_8w"], errors="coerce").to_numpy(dtype=float, na_value=np.nan)
    vol_factor = np.ones(len(agg), dtype=float)
    if use_volume:
        with np.errstate(invalid="ignore", divide="ignore"):
            vf = np.clip(d8 / vt, 0.0, 1.0)
        vol_factor = np.where(np.isfinite(d8) & (vt > 0), vf, 1.0)

    # confidence = week coverage x evidence depth (x stability if configured)
    coverage = agg["weeks_present"].to_numpy(dtype=float) / float(window_weeks)
    if confidence_method == "coverage_x_stability":
        stability = 1.0 / (1.0 + stability_dampener * agg["volatility_8w"].to_numpy(dtype=float))
        agg["confidence_8w"] = coverage * stability * vol_factor
    else:
        agg["confidence_8w"] = coverage * vol_factor

    # benchmark (mean of finite; NaN if none) and direction (first non-null; default higher_is_better)
    agg["benchmark_8w"] = gb["_bench"].mean().reindex(agg.index) if "_bench" in df.columns else np.nan
    if "direction" in df.columns:
        agg["direction"] = gb["direction"].first().reindex(agg.index).fillna("higher_is_better")
    else:
        agg["direction"] = "higher_is_better"

    agg["window_start"] = window_start
    agg["window_end"] = window_end

    # recency shift: mean(recent finite level) - mean(prior finite level); 0 if either side empty
    if include_recency:
        is_recent = df[period_col].isin(set(periods_sorted[-recent_weeks:]))
        recent_mean = df[is_recent].groupby(group_keys, dropna=False)["_lvl"].mean().reindex(agg.index)
        prior_mean = df[~is_recent].groupby(group_keys, dropna=False)["_lvl"].mean().reindex(agg.index)
        rs = (recent_mean - prior_mean).where(recent_mean.notna() & prior_mean.notna(), 0.0)
        agg["recency_shift"] = rs

    agg = agg.reset_index()

    # --- Window-level minimum total sample: drop thin-evidence windows (#2) ---
    min_window_weeks = float(cfg.get("min_window_weeks", 0))
    if has_denominator and min_window_weeks > 0 and not agg.empty:
        floor = agg["metric"].map(denom_min_by_metric).fillna(denom_default) * min_window_weeks
        d8 = pd.to_numeric(agg["denom_8w"], errors="coerce")
        thin = d8.notna() & (d8 < floor)
        n_thin = int(thin.sum())
        if n_thin:
            log.info(
                "aggregate_scores_window: dropped %d/%d windows below the %sx weekly-denominator "
                "floor (thin evidence).", n_thin, len(agg), min_window_weeks,
            )
        agg = agg[~thin].copy()

    ordered = cols_out + (["recency_shift"] if include_recency else [])
    return agg[ordered]