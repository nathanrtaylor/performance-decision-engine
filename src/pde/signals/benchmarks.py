from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd


def get_benchmark_value(
    metric: str,
    call_type: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    icp_client: Optional[str] = None,
) -> Optional[float]:
    """
    Returns a benchmark for a metric. Cohorts can differ materially (e.g. CRT is far lower in
    ATT Mobility than Verizon Soluto), so a benchmark may be set per icp_client.

    Resolution order (most specific first):
      by_icp_client[icp_client]  ->  by_call_type[call_type]  ->  default

    icp_client matching is case-insensitive (source data uses 'MOB-AT&T', the agents table
    'mob-at&t'). A metric may also be a bare scalar (shared default).

    Config expectation (example):
      benchmarks:
        crt:
          default: 1400
          by_icp_client:
            mob-at&t: 950
            pss-verizon: 1700
          by_call_type:
            claims: 1300
    """
    config = config or {}
    b = (config.get("benchmarks") or {}).get(metric)
    if b is None:
        return None
    if isinstance(b, (int, float)):
        return float(b)
    if not isinstance(b, dict):
        return None

    # most specific: per-cohort benchmark (case-insensitive)
    if icp_client is not None and not (isinstance(icp_client, float) and pd.isna(icp_client)):
        by_icp = b.get("by_icp_client") or {}
        norm = {str(k).strip().lower(): v for k, v in by_icp.items()}
        val = norm.get(str(icp_client).strip().lower())
        if val is not None:
            return float(val)

    # then call_type
    if call_type is not None:
        by_ct = b.get("by_call_type") or {}
        if by_ct.get(call_type) is not None:
            return float(by_ct[call_type])

    # fallback
    if b.get("default") is not None:
        return float(b["default"])
    return None


def benchmark_gap(value: float, benchmark: Optional[float]) -> Optional[float]:
    if benchmark is None:
        return None
    return float(value - benchmark)
