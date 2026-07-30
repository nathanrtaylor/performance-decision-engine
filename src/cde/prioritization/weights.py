from __future__ import annotations

from typing import Any, Dict, Optional


def _unwrap_root(obj: Any, root_key: str) -> Dict[str, Any]:
    """Support either {root_key: {...}} or an already-unwrapped {...}."""
    if not isinstance(obj, dict):
        return {}
    inner = obj.get(root_key)
    return inner if isinstance(inner, dict) else obj


def _metric_category(metric: str, config: Dict[str, Any]) -> Optional[str]:
    """Look up a metric's category from the metric_catalog."""
    mc = _unwrap_root(config.get("metric_catalog") or {}, "metric_catalog")
    metrics = mc.get("metrics") or {}
    meta = metrics.get(metric) or {}
    return meta.get("category")


def get_metric_weight(metric: str, call_type: Optional[str], config: Dict[str, Any]) -> float:
    """
    Resolve the versioned business weight for a metric.

    The governed priorities file (configs/priorities/*.yaml) expresses emphasis at the
    *category* level, with optional per-metric and per-call-type overrides:

        priorities:
          by_category:   {business: 1.0, tool_usage: 0.6, quality_behavior: 0.3}
          by_metric:     {transfer_rate: 1.2}          # optional override
          by_call_type:  {claims: {transfer_rate: 1.3}}  # optional, only if call types enabled

    Resolution order (first match wins):
      by_call_type[call_type][metric] -> by_metric[metric]
      -> by_category[metric_category] -> priorities.default (0.0)

    Note: the historical `priorities.weights.global`/`.by_call_type` shape is still honored
    as a fallback so older priority files keep working.
    """
    pri = config.get("priorities") or {}

    by_metric = pri.get("by_metric") or {}
    by_ct = pri.get("by_call_type") or {}
    by_cat = pri.get("by_category") or {}
    default_w = float(pri.get("default", 0.0))

    # 1) call-type-specific per-metric override
    if call_type and call_type in by_ct and isinstance(by_ct[call_type], dict) and metric in by_ct[call_type]:
        return float(by_ct[call_type][metric])

    # 2) explicit per-metric override
    if metric in by_metric:
        return float(by_metric[metric])

    # 3) category weight (the primary lever)
    cat = _metric_category(metric, config)
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
