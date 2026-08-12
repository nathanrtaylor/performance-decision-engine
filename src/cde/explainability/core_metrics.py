"""Per-expert "core metrics this week" summary for decision receipts.

Builds the compact per-metric block shown just below the coaching recommendation in each
decision receipt (and the expert-dashboard modal): for a configured, per-``icp_client``
set of "core" metrics, the most recent week's value, its benchmark for the expert's
cohort, the gap/direction, and the engine's existing 8-week trend.

All values come straight off ``eligible_signals`` (which already carries the cohort-
resolved ``benchmark``, ``gap``, ``direction`` and ``icp_client`` per agent x week x
metric); the trend reuses the ``(agent_id, period, call_type, metric)`` trend index the
receipt builder computes from ``scores_windowed`` — no new trend math.

Which metrics are "core" is governed by ``configs/mappings/core_metrics.yaml`` and differs
by cohort, resolved most-specific-first: ``by_icp_client[icp] -> default`` (case-insensitive,
mirroring :func:`cde.signals.benchmarks.get_benchmark_value`).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

__all__ = ["resolve_core_metric_keys", "build_core_metrics_index"]

_KEYS = ["agent_id", "period", "call_type"]


def resolve_core_metric_keys(icp_client: Optional[str], config: Dict[str, Any]) -> List[str]:
    """Ordered list of core metric keys for a cohort.

    Resolution order (most specific first): ``by_icp_client[icp_client] -> default``.
    ``icp_client`` matching is case-insensitive (source uses 'MOB-AT&T', the agents table
    'mob-at&t'). Returns ``[]`` when no ``core_metrics`` config is present.
    """
    cm = config.get("core_metrics") or {}
    # Tolerate both wrapped ({core_metrics: {...}}) and already-unwrapped shapes.
    if isinstance(cm.get("core_metrics"), dict):
        cm = cm["core_metrics"]

    if icp_client is not None and not (isinstance(icp_client, float) and pd.isna(icp_client)):
        by_icp = cm.get("by_icp_client") or {}
        norm = {str(k).strip().lower(): v for k, v in by_icp.items()}
        keys = norm.get(str(icp_client).strip().lower())
        if keys is not None:
            return [str(m) for m in keys]

    return [str(m) for m in (cm.get("default") or [])]


def build_core_metrics_index(
    eligible_signals: Optional[pd.DataFrame],
    trend_idx: Dict[Any, Dict[str, Any]],
    config: Dict[str, Any],
) -> Dict[Any, List[Dict[str, Any]]]:
    """Map ``(agent_id, period, call_type) -> [core-metric summary dict, ...]``.

    For each ``(agent_id, call_type)`` the block is anchored on that group's latest
    ``period`` in ``eligible_signals``; the configured core metrics for the agent's
    ``icp_client`` are emitted in config order, skipping any with no row that week.

    Each record uses the same key names as receipt ``drivers`` so the dashboard's
    existing compaction/rendering (``_driver`` / ``sevChip`` / ``meter``) is reused:
    ``{metric, value, benchmark, gap, direction, trend_8w, recency_shift}``.
    """
    if eligible_signals is None or eligible_signals.empty:
        return {}
    if not set(_KEYS + ["metric"]).issubset(eligible_signals.columns):
        return {}

    df = eligible_signals

    # Latest period per (agent_id, call_type) drives which week the block reports.
    latest = df.groupby(["agent_id", "call_type"])["period"].transform("max")
    latest_rows = df[df["period"] == latest]

    index: Dict[Any, List[Dict[str, Any]]] = {}
    for (agent_id, call_type), grp in latest_rows.groupby(["agent_id", "call_type"]):
        period = grp["period"].iloc[0]
        icp_client = grp["icp_client"].iloc[0] if "icp_client" in grp.columns else None
        keys = resolve_core_metric_keys(icp_client, config)
        if not keys:
            continue

        by_metric = grp.drop_duplicates("metric").set_index("metric")
        records: List[Dict[str, Any]] = []
        for m in keys:
            if m not in by_metric.index:
                continue  # no eligible data this week: skip (block only shows real data)
            row = by_metric.loc[m]
            trend = trend_idx.get((agent_id, period, call_type, m), {})
            records.append({
                "metric": m,
                "value": _num(row.get("value")),
                "benchmark": _num(row.get("benchmark")),
                "gap": _num(row.get("gap")),
                "direction": _str_or_none(row.get("direction")),
                "trend_8w": _num(trend.get("trend_8w")),
                "recency_shift": _num(trend.get("recency_shift")),
            })

        if records:
            index[(agent_id, period, call_type)] = records

    return index


def _num(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        f = float(x)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def _str_or_none(x: Any) -> Optional[str]:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    return str(x)
