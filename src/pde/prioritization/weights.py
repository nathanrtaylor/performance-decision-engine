from __future__ import annotations

from typing import Any, Dict, Optional

from pde.utils.config import unwrap_root as _unwrap_root


def _metric_category(metric: str, config: Dict[str, Any]) -> Optional[str]:
    """Look up a metric's category from the metric_catalog."""
    mc = _unwrap_root(config.get("metric_catalog") or {}, "metric_catalog")
    metrics = mc.get("metrics") or {}
    meta = metrics.get(metric) or {}
    return meta.get("category")


def _icp_client_block(pri: Dict[str, Any], icp_client: Optional[str]) -> Optional[Dict[str, Any]]:
    """The `priorities.by_icp_client[<cohort>]` override block for this agent's cohort, if any.

    Matched CASE-INSENSITIVELY (source data uses 'MOB-AT&T', configs are lowercase) -- same
    convention as benchmarks.get_benchmark_value.
    """
    if not icp_client:
        return None
    block = pri.get("by_icp_client") or {}
    if not block:
        return None
    key = str(icp_client).strip().lower()
    for k, v in block.items():
        if str(k).strip().lower() == key:
            return v or {}
    return None


def get_metric_weight(
    metric: str,
    call_type: Optional[str],
    config: Dict[str, Any],
    icp_client: Optional[str] = None,
) -> float:
    """
    Resolve the versioned business weight for a metric.

    The governed priorities file (configs/priorities/*.yaml) expresses emphasis at the
    *category* level, with optional per-metric, per-call-type, and per-cohort overrides:

        priorities:
          by_category:   {business: 1.0, tool_usage: 0.6, quality_behavior: 0.3}
          by_metric:     {transfer_rate: 1.2}          # optional global per-metric override
          by_call_type:  {claims: {transfer_rate: 1.3}}  # optional, only if call types enabled
          by_icp_client:                                 # optional per-cohort overrides
            "pss-verizon":
              by_category: {sell: 1.2}                   # override a category weight for this cohort
              by_metric:   {nsp100: 1.5}                 # override a metric weight for this cohort

    Resolution order (first match wins). Cohort overrides are the most specific:
      by_icp_client[icp].by_metric[metric] -> by_icp_client[icp].by_category[category]
      -> by_call_type[call_type][metric] -> by_metric[metric]
      -> by_category[metric_category] -> priorities.default (0.0)

    Note: the historical `priorities.weights.global`/`.by_call_type` shape is still honored
    as a fallback so older priority files keep working.
    """
    pri = config.get("priorities") or {}

    by_metric = pri.get("by_metric") or {}
    by_ct = pri.get("by_call_type") or {}
    by_cat = pri.get("by_category") or {}
    default_w = float(pri.get("default", 0.0))
    cat = _metric_category(metric, config)

    # 0) cohort (icp_client) overrides -- most specific; metric beats category within the cohort
    icp_block = _icp_client_block(pri, icp_client)
    if icp_block:
        icp_by_metric = icp_block.get("by_metric") or {}
        if metric in icp_by_metric:
            return float(icp_by_metric[metric])
        icp_by_cat = icp_block.get("by_category") or {}
        if cat and cat in icp_by_cat:
            return float(icp_by_cat[cat])

    # 1) call-type-specific per-metric override
    if call_type and call_type in by_ct and isinstance(by_ct[call_type], dict) and metric in by_ct[call_type]:
        return float(by_ct[call_type][metric])

    # 2) explicit per-metric override
    if metric in by_metric:
        return float(by_metric[metric])

    # 3) category weight (the primary lever)
    if cat and cat in by_cat:
        return float(by_cat[cat])

    # 4) legacy fallback: priorities.weights.{by_call_type,global}
    legacy = pri.get("weights") or {}
    if legacy:
        legacy_ct = legacy.get("by_call_type") or {}
        legacy_global = legacy.get("global") or {}
        if call_type and call_type in legacy_ct and metric in legacy_ct[call_type]:
            return float(legacy_ct[call_type][metric])
        if metric in legacy_global:
            return float(legacy_global[metric])
        return float(legacy.get("default", default_w))

    return default_w


def get_topic_weight(topic: str, call_type: Optional[str], config: Dict[str, Any]) -> float:
    """
    Optional topic-level weight (post metric->topic mapping). Defaults to 1.0 (neutral) so
    topic weighting is opt-in; the primary business lever is category weight on the metric.

        priorities:
          topic_weights:
            global: {"Reduce Client Transfer Rate": 1.1}
            by_call_type: {claims: {"Improve Resolution Rate": 1.2}}
    """
    pri = config.get("priorities") or {}
    weights = pri.get("topic_weights") or {}
    by_ct = weights.get("by_call_type") or {}
    global_w = weights.get("global") or {}
    default_w = float(weights.get("default", 1.0))

    if call_type and call_type in by_ct and topic in by_ct[call_type]:
        return float(by_ct[call_type][topic])
    if topic in global_w:
        return float(global_w[topic])
    return default_w
