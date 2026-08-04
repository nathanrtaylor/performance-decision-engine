# src/cde/engine/break_glass.py
"""
Tier 1 of the selection model: the BREAK-GLASS override.

Only metrics carrying a ``break_glass`` block in metric_catalog.yaml are
eligible. Over the latest ``recency_weeks`` weeks (a short recency window — NOT
the 8-week decision window), an agent trips break-glass on such a metric when it
is in the WORST ``worst_pct``% of its ICP_Client x metric cohort (raw cohort
percentile on the direction-adjusted "bad" axis) AND is below benchmark
(``bad_gap > 0``). A tripped metric overrides any theme (Tier 2) and any
ordinary single (Tier 3).

Computed off ``eligible_signals`` because that is the only frame carrying
``icp_client`` (the cohort dimension); ``scores_windowed`` drops it.

Backward compatibility: if no metric carries a ``break_glass`` block, this
returns an empty (well-formed) frame and selection behaves exactly as before.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from cde.engine.recommend import _conversation_type_for
from cde.prioritization.apply import _load_topic_map
from cde.temporal.aggregate import latest_n_periods
from cde.utils.logging import get_logger

log = get_logger(__name__)

_OUT_COLS = [
    "agent_id", "period", "call_type", "metric",
    "topic", "conversation_type",
    "icp_client", "value", "benchmark", "gap",
    "bad_gap", "norm_gap", "cohort_pct", "cohort_n", "severity",
]


def _metric_break_glass_specs(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Return {metric: {"worst_pct": float, "recency_weeks": int}} for every metric
    whose metric_catalog block sets break_glass.enabled truthy. Per-metric values
    fall back to the global active.yaml ``break_glass`` defaults.
    """
    mc = config.get("metric_catalog") or {}
    mc = mc.get("metric_catalog", mc) if isinstance(mc, dict) else {}
    metrics = mc.get("metrics") or {}

    defaults = config.get("break_glass") or {}
    default_worst = float(defaults.get("worst_pct", 10))
    default_recency = int(defaults.get("recency_weeks", 2))

    out: Dict[str, Dict[str, Any]] = {}
    for m, meta in metrics.items():
        bg = (meta or {}).get("break_glass") or {}
        if not bg.get("enabled", False):
            continue
        out[str(m)] = {
            "worst_pct": float(bg.get("worst_pct", default_worst)),
            "recency_weeks": int(bg.get("recency_weeks", default_recency)),
        }
    return out


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=_OUT_COLS)


def detect_break_glass(eligible_signals: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """
    Return one row per tripped (agent_id, period, call_type, metric). Empty
    well-formed frame when no metric is break-glass-flagged or nothing trips.
    ``period`` is set to the latest period in the data (aligns with the windowed
    decision grain used by Tier 2/3).
    """
    specs = _metric_break_glass_specs(config)
    if not specs or eligible_signals is None or eligible_signals.empty:
        return _empty()

    df = eligible_signals.copy()
    required = {"agent_id", "period", "call_type", "metric", "gap", "direction"}
    missing = required - set(df.columns)
    if missing:
        log.info("break_glass: eligible_signals missing %s; skipping override.", sorted(missing))
        return _empty()
    if "icp_client" not in df.columns:
        log.info("break_glass: eligible_signals has no icp_client column; skipping override.")
        return _empty()

    df = df[df["metric"].isin(specs)].copy()
    if df.empty:
        return _empty()

    df["period"] = pd.to_datetime(df["period"], errors="coerce")
    df = df[df["period"].notna()]
    if df.empty:
        return _empty()

    # Decision period to stamp on outputs = the latest period overall (== window_end).
    decision_period = max(latest_n_periods(df, "period", 1))

    df["gap"] = pd.to_numeric(df["gap"], errors="coerce")
    df["benchmark"] = pd.to_numeric(df.get("benchmark"), errors="coerce")
    df["value"] = pd.to_numeric(df.get("value"), errors="coerce")

    # Per-metric recency slice (metrics may set their own recency_weeks).
    slices: List[pd.DataFrame] = []
    for metric, spec in specs.items():
        sub = df[df["metric"] == metric]
        if sub.empty:
            continue
        keep = set(latest_n_periods(sub, "period", spec["recency_weeks"]))
        slices.append(sub[sub["period"].isin(keep)])
    if not slices:
        return _empty()
    recent = pd.concat(slices, ignore_index=True)

    # Collapse to the windowed-mean grain over the recency slice.
    grp = ["agent_id", "icp_client", "call_type", "metric"]
    agg = recent.groupby(grp, dropna=False, sort=True).agg(
        gap=("gap", "mean"),
        benchmark=("benchmark", "mean"),
        value=("value", "mean"),
        direction=("direction", "first"),
    ).reset_index()

    lower_is_better = agg["direction"].astype(str).eq("lower_is_better")
    agg["bad_gap"] = agg["gap"].where(lower_is_better, -agg["gap"])

    # Cohort percentile within (icp_client, metric, call_type): worst = highest bad_gap.
    cohort_keys = ["icp_client", "metric", "call_type"]
    agg["cohort_pct"] = agg.groupby(cohort_keys, dropna=False)["bad_gap"].rank(
        pct=True, method="average"
    )
    agg["cohort_n"] = agg.groupby(cohort_keys, dropna=False)["bad_gap"].transform("size")

    # Trip: in the worst worst_pct% of the cohort AND below benchmark.
    worst_pct = agg["metric"].map(lambda m: specs[m]["worst_pct"]).astype(float)
    threshold = 1.0 - (worst_pct / 100.0)
    tripped = agg[(agg["cohort_pct"] >= threshold) & (agg["bad_gap"] > 0)].copy()
    if tripped.empty:
        return _empty()

    # Scale-free magnitude: how many benchmark-widths past the line.
    bench_abs = tripped["benchmark"].abs()
    tripped["norm_gap"] = (tripped["bad_gap"] / bench_abs).where(bench_abs > 0, tripped["bad_gap"])

    # Severity primary = cohort depth (scale-free); tie-break by norm_gap then metric name.
    tripped["severity"] = tripped["cohort_pct"]

    tm = _load_topic_map(config)
    metric_to_topic = tm.get("metric_to_topic") or {}
    tripped["topic"] = tripped["metric"].map(metric_to_topic).fillna(tripped["metric"])
    tripped["conversation_type"] = tripped["topic"].apply(lambda t: _conversation_type_for(t, config))
    tripped["period"] = decision_period

    tripped = tripped[_OUT_COLS].reset_index(drop=True)
    return tripped


def top_break_glass_per_agent(break_glass: pd.DataFrame) -> pd.DataFrame:
    """
    Pick the single highest-severity tripped metric per (agent, period, call_type):
    cohort_pct desc, then norm_gap desc, then metric name asc (deterministic).
    """
    if break_glass is None or break_glass.empty:
        return _empty()
    df = break_glass.sort_values(
        ["severity", "norm_gap", "metric"], ascending=[False, False, True], kind="mergesort"
    )
    top = df.groupby(["agent_id", "period", "call_type"], as_index=False, sort=True).head(1).copy()
    return top.reset_index(drop=True)
