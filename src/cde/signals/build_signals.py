# src/cde/signals/build_signals.py

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from cde.signals.benchmarks import get_benchmark_value, benchmark_gap
from cde.utils.config import unwrap_root
from cde.utils.logging import get_logger
from typing import Any, Optional
from cde.signals.load_inputs import load_normalized_for_signals

log = get_logger(__name__)


def _to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        if isinstance(x, float) and pd.isna(x):
            return None
        return float(x)
    except Exception:
        return None


def _safe_pct_change(curr: Optional[float], prev: Optional[float]) -> Optional[float]:
    if curr is None or prev is None:
        return None
    if prev == 0:
        return None
    return float((curr - prev) / abs(prev))


def _compute_value_row(
    numerator: Optional[float],
    denominator: Optional[float],
    calculation: Optional[str],
    raw_value: Optional[float],
    prefer_value: bool,
    default_calculation: Optional[str],
    handlers: Dict[str, Any],
) -> Optional[float]:
    """
    Deterministic computation:
      - if prefer_value and raw_value present -> use raw_value
      - else compute from numerator/denominator based on calculation or default_calculation
    """
    if prefer_value and raw_value is not None:
        return raw_value

    calc = calculation or default_calculation
    if not calc:
        return raw_value  # last resort

    calc = str(calc).strip().lower()
    # normalize calc-name drift: metric_catalog uses "average", source handlers use "avg"
    if calc == "average":
        calc = "avg"
    handler = handlers.get(calc) or {}

    # supported patterns: rate/avg = numerator/denominator; sum/count/score = numerator
    if calc in ("rate", "avg"):
        if numerator is None or denominator is None:
            return None
        denom_min = _to_float(handler.get("denominator_min")) or 1.0
        if denominator < denom_min or denominator == 0:
            return None
        return float(numerator / denominator)

    if calc in ("sum", "count", "score"):
        return numerator

    # unknown calc => fall back to raw value if present
    return raw_value

def build_signals(normalized: Dict[str, pd.DataFrame], config: Dict[str, Any]) -> pd.DataFrame:
    """
    Build long-form signals by UNION-ing multiple tall-skinny sources as defined in:
      - config["source_catalog"] (sources + schemas + computation rules)
      - config["metric_catalog"] (canonical metrics + source bindings + direction/category/benchmark)

    Call type "off switch":
      If config["call_type_mode"] == "disabled", then call_type is collapsed to
      config["default_call_type"] (default: "all_calls"), and all call-type specific
      segmentation/benchmarks/overrides operate in that single bucket.

    Output columns (opinionated):
      agent_id, period, call_type, metric, category, direction,
      source, source_metric_key,
      numerator, denominator, calculation, value,
      prev_value, trend, volatility, confidence,
      benchmark, gap
    """
    source_catalog = unwrap_root(config.get("source_catalog"), "source_catalog")
    metric_catalog = unwrap_root(config.get("metric_catalog"), "metric_catalog")

    sources_cfg = (source_catalog.get("sources") or {})
    metrics_cfg = (metric_catalog.get("metrics") or {})
    governance = (metric_catalog.get("governance") or {})
    disallow_unknown = bool(governance.get("disallow_unknown_metrics", True))

    if not sources_cfg:
        raise ValueError("source_catalog.sources is empty or missing.")
    if not metrics_cfg:
        raise ValueError("metric_catalog.metrics is empty or missing.")

    call_type_mode = config.get("call_type_mode", "enabled")
    default_call_type = config.get("default_call_type", "all_calls")

    # Build mapping from (source, source_metric_key) -> canonical metric name
    source_key_to_metric: Dict[Tuple[str, str], str] = {}
    metric_meta: Dict[str, Dict[str, Any]] = {}
    for canonical_metric, m in metrics_cfg.items():
        src = m.get("source")
        key = m.get("source_metric_key")
        if not src or not key:
            raise ValueError(f"Metric '{canonical_metric}' missing source or source_metric_key in metric_catalog.")
        source_key_to_metric[(str(src), str(key))] = canonical_metric
        metric_meta[canonical_metric] = m

    # Union all sources into one standardized long table
    frames = []
    for source_name, s in sources_cfg.items():
        if source_name not in normalized:
            log.info("Source table '%s' not found in normalized inputs; skipping.", source_name)
            continue

        df = normalized[source_name].copy()

        schema = (s.get("schema") or {})

        # ✅ Skip dimension/non-metric sources (e.g., agents)
        if (s.get("type") == "dimension") or (schema.get("metric_key") in (None, "null", "")):
            log.info("Skipping non-metric source '%s' in build_signals()", source_name)
            continue
        
        entity_keys = (schema.get("entity_keys") or {})
        agent_col = entity_keys.get("agent_id", "agent_id")
        period_col = entity_keys.get("period", "week_start")
        call_type_col = entity_keys.get("call_type", "call_type")

        metric_key_col = schema.get("metric_key")
        num_col = schema.get("numerator")
        den_col = schema.get("denominator")
        calc_col = schema.get("calculation")
        val_col = schema.get("value")

        if not metric_key_col:
            raise ValueError(f"Source '{source_name}' missing schema.metric_key in source_catalog.")

        # Create standardized columns
        out = pd.DataFrame()
        out["agent_id"] = df[agent_col]
        # Canonicalize period for this source
        if "period" in df.columns:
            out["period"] = df["period"]
        elif period_col in df.columns:
            out["period"] = df[period_col]
        elif "week_ending" in df.columns:
            out["period"] = df["week_ending"]
        elif "week_start" in df.columns:
            out["period"] = df["week_start"]
        else:
            raise KeyError(
                f"build_signals: period column not found for source '{source_name}'. "
                f"Looked for 'period', '{period_col}', 'week_ending', 'week_start'. "
                f"Available columns: {list(df.columns)}"
        )

        if call_type_col in df.columns:
            out["call_type"] = df[call_type_col]
        else:
            out["call_type"] = None

        # Call type off switch: collapse all call types into one bucket
        if call_type_mode == "disabled":
            out["call_type"] = default_call_type

        out["source"] = source_name
        out["source_metric_key"] = df[metric_key_col].astype(str)

        out["numerator"] = pd.to_numeric(df[num_col], errors="coerce") if num_col and num_col in df.columns else np.nan
        out["denominator"] = pd.to_numeric(df[den_col], errors="coerce") if den_col and den_col in df.columns else np.nan

        out["calculation"] = df[calc_col] if calc_col and calc_col in df.columns else None
        out["value_raw"] = pd.to_numeric(df[val_col], errors="coerce") if val_col and val_col in df.columns else np.nan

        # Carry cohort (icp_client) from the source when present (agent_metrics has it per row);
        # this gives full coverage for per-cohort benchmark lookup (the agents-table join is sparse).
        out["icp_client"] = df["icp_client"] if "icp_client" in df.columns else np.nan

        frames.append(out)

    if not frames:
        raise ValueError("No source tables were found/loaded. Check normalized inputs and source_catalog.yaml.")

    base = pd.concat(frames, ignore_index=True)

    # --------------------------------------------------
    # Enrich with org fields from agents.csv (if present)
    # --------------------------------------------------

    agents_raw = normalized.get("agents")

    if agents_raw is not None and not agents_raw.empty:

        agents = agents_raw.copy()

        # Standardize to canonical keys
        agents["agent_id"] = agents["agent_id"].astype(str)
        agents["period"] = agents["week_ending"]

        agents = agents.drop_duplicates(["agent_id", "period"])

        # Ensure base keys are strings for safe join
        base["agent_id"] = base["agent_id"].astype(str)

        base = base.merge(
            agents[["agent_id", "period", "mascot", "icp_client", "coach", "coach_id"]],
            on=["agent_id", "period"],
            how="left",
            suffixes=("", "_agt"),
        )
        # Prefer the source icp_client (full coverage); fall back to the agents table where missing.
        if "icp_client_agt" in base.columns:
            base["icp_client"] = base["icp_client"].fillna(base["icp_client_agt"])
            base = base.drop(columns=["icp_client_agt"])


    # Map to canonical metric names
    base["metric"] = [
        source_key_to_metric.get((src, key))
        for src, key in zip(base["source"].tolist(), base["source_metric_key"].tolist())
    ]

    if disallow_unknown:
        base = base[base["metric"].notna()].copy()
    else:
        base["metric"] = base["metric"].fillna(base["source_metric_key"])

    # Attach category, direction, benchmark lookup key
    base["category"] = base["metric"].map(lambda m: (metric_meta.get(m) or {}).get("category"))
    base["direction"] = base["metric"].map(lambda m: (metric_meta.get(m) or {}).get("direction", "higher_is_better"))
    base["benchmark_key"] = base["metric"].map(
        lambda m: ((metric_meta.get(m) or {}).get("benchmark") or {}).get("key", m)
    )

    # Compute value deterministically per source computation rules (VECTORIZED; equivalent to the
    # former per-row _compute_value_row). value = the raw calc column when prefer_value is set and
    # present; otherwise derived from numerator/denominator per the effective calculation.
    def _norm_calc(x: Any) -> Optional[str]:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return None
        s = str(x).strip().lower()
        if not s:
            return None
        return "avg" if s == "average" else s

    src_prefer = {s: bool((c.get("computation") or {}).get("prefer_value_column_if_present", True))
                  for s, c in sources_cfg.items()}
    src_default_calc = {s: _norm_calc((c.get("computation") or {}).get("default_calculation"))
                        for s, c in sources_cfg.items()}
    src_handlers = {s: ((c.get("computation") or {}).get("calculation_handlers") or {})
                    for s, c in sources_cfg.items()}
    metric_expected = {m: _norm_calc((meta.get("computation_override") or {}).get("expected_calculation"))
                       for m, meta in metric_meta.items()}

    num = pd.to_numeric(base["numerator"], errors="coerce")
    den = pd.to_numeric(base["denominator"], errors="coerce")
    raw = pd.to_numeric(base["value_raw"], errors="coerce")

    # effective calc per row: row calculation -> metric expected_calculation -> source default
    rowcalc = base["calculation"].map(_norm_calc)
    eff = rowcalc.where(rowcalc.notna(), base["metric"].map(metric_expected))
    eff = eff.where(eff.notna(), base["source"].map(src_default_calc))
    prefer = base["source"].map(src_prefer).fillna(True).astype(bool)

    # per-row denominator floor for rate/avg (from source handler; falsy -> 1.0)
    def _handler_denom_min(source: str, calc: str) -> float:
        h = (src_handlers.get(source) or {}).get(calc) or {}
        try:
            dm = float(h.get("denominator_min"))
        except (TypeError, ValueError):
            dm = None
        return dm or 1.0

    dmin = pd.Series(1.0, index=base.index)
    rate_all = eff.isin(["rate", "avg"])
    if rate_all.any():
        rk = list(zip(base.loc[rate_all, "source"], eff[rate_all]))
        rk_map = {k: _handler_denom_min(k[0], k[1]) for k in set(rk)}
        dmin.loc[rate_all] = [rk_map[k] for k in rk]

    value = pd.Series(np.nan, index=base.index, dtype="float64")
    use_raw = prefer & raw.notna()
    value[use_raw] = raw[use_raw]

    rem = ~use_raw
    is_rate = rem & rate_all
    is_sum = rem & eff.isin(["sum", "count", "score"])
    other = rem & ~is_rate & ~is_sum        # eff None/unknown -> fall back to raw value
    rate_ok = is_rate & num.notna() & den.notna() & (den >= dmin) & (den != 0)

    value[is_sum] = num[is_sum]
    value[rate_ok] = (num / den)[rate_ok]   # invalid rate rows stay NaN (matches returning None)
    value[other] = raw[other]

    base["value"] = value

    # Sort for time-based calculations
    base = base.sort_values(["agent_id", "call_type", "metric", "period"]).reset_index(drop=True)

    # Previous value and trend (vectorized pct change; None when prev is missing or zero)
    base["prev_value"] = base.groupby(["agent_id", "call_type", "metric"])["value"].shift(1)
    _v, _p = base["value"], base["prev_value"]
    base["trend"] = ((_v - _p) / _p.abs()).where(_v.notna() & _p.notna() & (_p != 0))

    # Volatility: rolling std over last N periods (per agent+call_type+metric)
    window = int((config.get("signal_window") or {}).get("volatility_periods", 4))
    base["volatility"] = (
        base.groupby(["agent_id", "call_type", "metric"])["value"]
        .rolling(window, min_periods=2)
        .std()
        .reset_index(level=[0, 1, 2], drop=True)
    )

    # Normalize cohort casing (source uses 'MOB-AT&T', agents table 'mob-at&t') so per-cohort
    # benchmark keys, signals, and the dashboard all align on one lowercase form.
    if "icp_client" not in base.columns:
        base["icp_client"] = np.nan
    base["icp_client"] = (
        base["icp_client"].astype("string").str.strip().str.lower().replace({"": pd.NA})
    )

    # Benchmark + gap: resolve per DISTINCT (benchmark_key, call_type, icp_client) combo (a few
    # dozen), then map onto rows - avoids ~1M get_benchmark_value calls. (NA icp -> None so the
    # cache keys hash/compare correctly; get_benchmark_value treats both as "no cohort".)
    bk_list = base["benchmark_key"].tolist()
    ct_list = base["call_type"].tolist()
    icp_list = [None if pd.isna(x) else x for x in base["icp_client"].tolist()]
    bench_cache = {
        k: get_benchmark_value(k[0], k[1], config, icp_client=k[2])
        for k in set(zip(bk_list, ct_list, icp_list))
    }
    base["benchmark"] = [bench_cache[(b, c, i)] for b, c, i in zip(bk_list, ct_list, icp_list)]

    _val = pd.to_numeric(base["value"], errors="coerce")
    _bench = pd.to_numeric(base["benchmark"], errors="coerce")
    base["gap"] = (_val - _bench).where(_val.notna() & _bench.notna())

    # Confidence (deterministic, explainable):
    # - present factor (value exists)
    # - denominator factor (if denom exists) using per-metric override > source default > global default
    # - volatility penalty (normalized within period+call_type+metric)
    present = (~pd.isna(pd.to_numeric(base["value"], errors="coerce"))).astype(float)

    # denominator floor: global default for confidence (not gating; gating happens in thresholds)
    global_den_min = float(
        (((config.get("thresholds") or {}).get("signal_thresholds") or {}).get("global") or {}).get(
            "min_denominator_default", 10
        )
    )

    src_default_den = {}
    for src_name, s_cfg in sources_cfg.items():
        dq = (s_cfg.get("data_quality") or {})
        src_default_den[src_name] = float(dq.get("default_denominator_min", global_den_min))

    per_metric_den_min = {}
    for m, meta in metric_meta.items():
        over = meta.get("computation_override") or {}
        if "denominator_min" in over:
            per_metric_den_min[m] = float(over["denominator_min"])

    denom_vals = pd.to_numeric(base["denominator"], errors="coerce")
    # per-metric override -> source default -> global (vectorized)
    _pm = base["metric"].map(per_metric_den_min)
    _sd = base["source"].map(src_default_den)
    denom_min_vals = _pm.where(_pm.notna(), _sd).fillna(global_den_min)

    denom_factor = pd.Series(1.0, index=base.index)
    has_denom = denom_vals.notna()
    denom_factor.loc[has_denom] = (denom_vals.loc[has_denom] / denom_min_vals.loc[has_denom]).clip(lower=0.0, upper=1.0)

    # volatility normalization within (period, call_type, metric)
    vol = pd.to_numeric(base["volatility"], errors="coerce")
    vol_grp = base.groupby(["period", "call_type", "metric"])["volatility"]
    vol_mean = vol_grp.transform("mean")
    vol_std = vol_grp.transform("std").replace(0, np.nan)
    vol_z = (vol - vol_mean) / vol_std
    vol_z = pd.to_numeric(vol_z, errors="coerce").fillna(0.0)
    vol_penalty = (1 / (1 + np.exp(-vol_z))).clip(0.0, 1.0)
    vol_factor = (1.0 - 0.5 * vol_penalty).clip(0.0, 1.0)

    base["confidence"] = (present * (0.6 * denom_factor + 0.4 * vol_factor)).clip(0.0, 1.0)

    # Final column selection
    keep_cols = [
        "agent_id",
        "period",
        "mascot",
        "icp_client",
        "coach",
        "coach_id",
        "call_type",
        "category",
        "direction",
        "source",
        "metric",
        "numerator",
        "denominator",
        "calculation",
        "value",
        "prev_value",
        "trend",
        "volatility",
        "confidence",
        "benchmark",
        "gap",
    ]
    out = base[keep_cols].copy()

    return out