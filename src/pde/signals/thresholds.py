from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ThresholdResult:
    eligible_signals: pd.DataFrame
    excluded_signals: pd.DataFrame


def apply_signal_thresholds(signals: pd.DataFrame, config: Dict[str, Any]) -> ThresholdResult:
    """
    Apply deterministic signal gating using configs/thresholds/signal_thresholds.yaml (loaded into config["thresholds"]).

    Adds a governed mode switch:
      - mode: "development" => minimal gating (value present + IDs; skips reference/magnitude/trend gates)
      - mode: "production"  => enforce reference/magnitude/trend rules (fail-closed)
    """
    thr = (config.get("thresholds") or {}).get("signal_thresholds") or {}
    if not thr:
        # If no thresholds configured, treat everything as eligible (not recommended)
        eligible = signals.copy()
        excluded = signals.iloc[0:0].copy()
        excluded["exclusion_reasons"] = []
        return ThresholdResult(eligible_signals=eligible, excluded_signals=excluded)

    mode = str(thr.get("mode", "production")).lower().strip()  # development | production

    sig = signals.copy()

    # ---- Required columns: minimal set differs by mode
    # Development mode should not require benchmark-derived fields.
    required_min = {"agent_id", "period", "call_type", "metric", "value", "direction"}
    required_prod = required_min | {"confidence"}  # production enforces confidence by default; can be overridden

    required_cols = required_min if mode == "development" else required_prod
    missing = required_cols - set(sig.columns)
    if missing:
        raise ValueError(f"Signals missing required cols for thresholding ({mode}): {sorted(missing)}")

    # Ensure optional columns exist so later logic doesn't KeyError
    # (These may be entirely null early on; that's okay.)
    for optional in ["trend", "confidence", "volatility", "gap", "denominator"]:
        if optional not in sig.columns:
            sig[optional] = np.nan

    # ---- Compute a distribution z-score (used when benchmark gap isn't available)
    # z is computed within (period, call_type, metric)
    # Note: if a group has std==0 or <2 rows, z will be NaN.
    grp = sig.groupby(["period", "call_type", "metric"])["value"]
    mean = grp.transform("mean")
    std = grp.transform("std").replace(0, np.nan)
    sig["_z_raw"] = (sig["value"] - mean) / std
    # orient so "worse" => higher
    sig["_z_bad"] = np.where(sig["direction"] == "higher_is_better", -sig["_z_raw"], sig["_z_raw"])
    sig["_z_bad"] = pd.to_numeric(sig["_z_bad"], errors="coerce")

    # ---- Determine category per metric if metric_catalog is loaded (recommended)
    metric_catalog = (config.get("metric_catalog") or {}).get("metric_catalog") or (config.get("metric_catalog") or {})
    metrics_meta = (metric_catalog.get("metrics") or {}) if isinstance(metric_catalog, dict) else {}
    sig["_category"] = sig["metric"].map(lambda m: (metrics_meta.get(m) or {}).get("category", "unknown"))

    # Candidate rules (global) — production defaults are "fail closed"
    candidate_rules = thr.get("candidate_rules") or {}
    require_ref = bool(candidate_rules.get("require_reference_point", True))
    require_bad_mag = bool(candidate_rules.get("require_bad_magnitude", True))
    require_bad_trend = bool(candidate_rules.get("require_bad_trend", False))
    only_worsening = bool(candidate_rules.get("only_worsening", False))
    # Development mode: relax gates that depend on benchmarks/history.
    if mode == "development":
        require_ref = require_bad_mag = require_bad_trend = only_worsening = False

    # ---- Resolve thresholds once per DISTINCT (metric, call_type, category), then map to columns.
    #      (Precedence handled in _resolve_thresholds: by_metric > by_call_type+category > by_category > global.)
    combos = sig[["metric", "call_type", "_category"]].drop_duplicates().itertuples(index=False)
    resolved = {
        (m, ct, cat): _resolve_thresholds(thr, metric=m, call_type=ct, category=cat)
        for m, ct, cat in combos
    }
    keys = list(zip(sig["metric"], sig["call_type"], sig["_category"]))

    def _tfield(name: str) -> pd.Series:
        # float dtype so missing thresholds are NaN (not Python None), keeping comparisons vectorizable
        return pd.Series([_to_float(resolved[k].get(name)) for k in keys], index=sig.index, dtype="float64")

    min_conf = _tfield("min_confidence").fillna(0.0)
    max_vol = _tfield("max_volatility")
    denom_min = _tfield("min_denominator_default")
    min_gap = _tfield("min_abs_gap_from_benchmark")
    min_bad_z = _tfield("min_bad_z")
    min_bad_trend = _tfield("min_bad_trend_pct")

    # ---- Coerce signal columns once ----
    val = pd.to_numeric(sig["value"], errors="coerce")
    conf = pd.to_numeric(sig["confidence"], errors="coerce")
    vol = pd.to_numeric(sig["volatility"], errors="coerce")
    denom = pd.to_numeric(sig["denominator"], errors="coerce")
    gap = pd.to_numeric(sig["gap"], errors="coerce")
    z_bad = pd.to_numeric(sig["_z_bad"], errors="coerce")
    trend = pd.to_numeric(sig["trend"], errors="coerce")
    higher = sig["direction"].eq("higher_is_better")

    # ---- Vectorized gates (same reasons + precedence as the former per-row loop) ----
    missing_value = val.isna()
    low_conf = conf.notna() & (conf < min_conf)
    missing_conf = (mode != "development") & conf.isna() & (min_conf > 0.0)
    high_vol = max_vol.notna() & vol.notna() & (vol > max_vol)
    low_denom = denom_min.notna() & denom.notna() & (denom < denom_min)
    no_ref = (gap.isna() & z_bad.isna()) & require_ref

    # magnitude: prefer benchmark gap, else distribution z; fail-closed when neither is available
    bad_gap = gap.where(~higher, -gap)
    mag_pass = pd.Series(
        np.where(
            (gap.notna() & min_gap.notna()).to_numpy(), (bad_gap >= min_gap).to_numpy(),
            np.where((z_bad.notna() & min_bad_z.notna()).to_numpy(), (z_bad >= min_bad_z).to_numpy(), False),
        ),
        index=sig.index,
    )
    insuff_mag = (~mag_pass) & require_bad_mag

    # trend: direction-adjusted "bad" pct change
    bad_trend_val = trend.where(~higher, -trend)
    bad_trend = trend.notna() & min_bad_trend.notna() & (bad_trend_val >= min_bad_trend)
    insuff_trend = (~bad_trend) & require_bad_trend
    not_worsening = (~bad_trend) & only_worsening

    reason_masks = {
        "MISSING_VALUE": missing_value,
        "MISSING_CONFIDENCE": missing_conf,
        "LOW_CONFIDENCE": low_conf,
        "HIGH_VOLATILITY": high_vol,
        "LOW_DENOMINATOR": low_denom,
        "NO_REFERENCE_POINT": no_ref,
        "INSUFFICIENT_MAGNITUDE": insuff_mag,
        "INSUFFICIENT_BAD_TREND": insuff_trend,
        "NOT_WORSENING": not_worsening,
    }
    rdf = pd.DataFrame(reason_masks).fillna(False)
    excluded_mask = rdf.any(axis=1)

    eligible = sig[~excluded_mask].copy()
    eligible = eligible.drop(
        columns=[c for c in ["_z_raw", "_z_bad", "_category"] if c in eligible.columns], errors="ignore"
    )

    # ---- Excluded frame: build reason lists only for the excluded subset ----
    cols = list(rdf.columns)
    arr = rdf.loc[excluded_mask, cols].to_numpy()
    reasons_lists = [[cols[j] for j in np.nonzero(arr[i])[0]] for i in range(arr.shape[0])]
    em = excluded_mask.to_numpy()
    excluded = pd.DataFrame(
        {
            "agent_id": sig.loc[excluded_mask, "agent_id"].to_numpy(),
            "period": sig.loc[excluded_mask, "period"].to_numpy(),
            "call_type": sig.loc[excluded_mask, "call_type"].to_numpy(),
            "metric": sig.loc[excluded_mask, "metric"].to_numpy(),
            "category": sig.loc[excluded_mask, "_category"].to_numpy(),
            "value": val[em].to_numpy(),
            "gap": gap[em].to_numpy(),
            "z_bad": z_bad[em].to_numpy(),
            "trend": trend[em].to_numpy(),
            "confidence": conf[em].to_numpy(),
            "volatility": vol[em].to_numpy(),
            "exclusion_reasons": reasons_lists,
            "threshold_mode": [mode] * int(em.sum()),
        }
    )

    return ThresholdResult(eligible_signals=eligible, excluded_signals=excluded)


def _resolve_thresholds(thr: Dict[str, Any], metric: str, call_type: Optional[str], category: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    # global
    out.update(thr.get("global") or {})

    # by_category
    by_cat = thr.get("by_category") or {}
    if category in by_cat:
        out.update(by_cat[category] or {})

    # by_call_type
    by_ct = thr.get("by_call_type") or {}
    if call_type and call_type in by_ct:
        ct_block = by_ct[call_type] or {}
        if isinstance(ct_block, dict) and category in ct_block:
            out.update(ct_block[category] or {})

    # by_metric (highest precedence)
    by_metric = thr.get("by_metric") or {}
    if metric in by_metric:
        out.update(by_metric[metric] or {})

    return out


def _magnitude_pass(row: pd.Series, t: Dict[str, Any]) -> bool:
    """
    If benchmark gap exists, compare "bad_gap" to min_abs_gap_from_benchmark.
    Else use z_bad >= min_bad_z (distribution severity).
    """
    gap = _to_float(row.get("gap"))
    min_gap = _to_float(t.get("min_abs_gap_from_benchmark"))
    if gap is not None and min_gap is not None:
        direction = row.get("direction")
        bad_gap = -gap if direction == "higher_is_better" else gap
        return bad_gap >= min_gap

    z_bad = _to_float(row.get("_z_bad"))
    min_bad_z = _to_float(t.get("min_bad_z"))
    if z_bad is not None and min_bad_z is not None:
        return z_bad >= min_bad_z

    # Fail closed in production when magnitude is required and no reference is available
    return False


def _is_bad_trend(trend: Optional[float], direction: Optional[str], min_bad_trend_pct: Optional[float]) -> bool:
    if trend is None or min_bad_trend_pct is None:
        return False
    bad = (-trend) if direction == "higher_is_better" else trend
    return bad >= min_bad_trend_pct


def _to_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    if x is None:
        return default
    try:
        if isinstance(x, float) and pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default
