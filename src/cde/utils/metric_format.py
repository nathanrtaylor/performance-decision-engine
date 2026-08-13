"""Per-metric display formatting (presentation only).

Metric values are stored in native scale (``nsp100`` per-call ~0-1, rates 0-1, behaviors 0-1,
``erp`` 0-100, times in seconds). The metric catalog's optional ``display`` block controls how a
value is *shown* — scaled, rounded, with a suffix — without changing the stored value used by
scoring/selection/benchmarks.

``display`` resolves per-metric over the metric's ``category_defaults[category].display``::

    display:
      scale: 100      # multiply the raw value by this (default 1)
      suffix: "%"     # appended after the number (default "")
      decimals: 1     # fixed decimal places (default: none -> caller keeps its own formatting)

Applied uniformly to value/benchmark/gap (they share a scale per metric). Keep ``format_value``
in sync with the JS twin ``fmtMetric``/``fmtNum`` in ``reporting/expert_dashboard.py``.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from cde.utils.config import unwrap_root

__all__ = ["display_map", "format_value", "resolve_display"]

_SPEC_KEYS = ("scale", "suffix", "decimals")


def _catalog(config: Dict[str, Any]) -> Dict[str, Any]:
    return unwrap_root((config or {}).get("metric_catalog") or {}, "metric_catalog")


def _clean_spec(raw: Any) -> Dict[str, Any]:
    """Keep only recognized display keys with non-None values."""
    if not isinstance(raw, dict):
        return {}
    return {k: raw[k] for k in _SPEC_KEYS if raw.get(k) is not None}


def resolve_display(catalog: Dict[str, Any], metric: str) -> Dict[str, Any]:
    """Merge a metric's ``display`` over its category default. ``{}`` when neither is set."""
    entry = (catalog.get("metrics") or {}).get(metric) or {}
    cat_default = ((catalog.get("category_defaults") or {}).get(entry.get("category")) or {})
    return {**_clean_spec(cat_default.get("display")), **_clean_spec(entry.get("display"))}


def display_map(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """``{metric -> resolved display spec}`` for every metric that has one (direct or via category)."""
    catalog = _catalog(config)
    out: Dict[str, Dict[str, Any]] = {}
    for metric in (catalog.get("metrics") or {}):
        spec = resolve_display(catalog, metric)
        if spec:
            out[metric] = spec
    return out


def format_value(value: Any, spec: Optional[Dict[str, Any]]) -> Optional[str]:
    """Format a raw metric value per ``spec``.

    Returns ``None`` when there is no usable spec (so the caller keeps its own default
    formatting), and the em-dash for missing/non-numeric values.
    """
    if not spec:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if pd.isna(v):
        return "—"
    try:
        v = v * float(spec.get("scale", 1))
    except (TypeError, ValueError):
        pass
    decimals = spec.get("decimals")
    if decimals is None:
        s = f"{v:g}"
    else:
        try:
            d = max(0, int(decimals))
        except (TypeError, ValueError):
            d = 0
        s = f"{v:,.{d}f}"
    return f"{s}{spec.get('suffix') or ''}"
