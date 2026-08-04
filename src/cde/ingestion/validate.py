from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from cde.utils.frames import require_cols as _require_cols


def validate_inputs(normalized: Dict[str, pd.DataFrame], config: Dict[str, Any]) -> None:
    """
    Opinionated minimum checks:
    - required entity keys exist (agent_id, week_start or date depending on config)
    - no duplicated entity rows in key tables
    - metric values are numeric where expected
    """
    entity = config.get("entity_keys", {"agent_id": "agent_id", "period": "week_start"})
    agent_key = entity["agent_id"]
    period_key = entity["period"]

    required_tables = config.get("required_tables", [])
    for t in required_tables:
        if t not in normalized:
            raise ValueError(f"Required table missing after normalization: '{t}'")

    # Validate core “mart-like” table if present
    if "engine_inputs" in normalized:
        df = normalized["engine_inputs"]
        _require_cols(df, [agent_key, period_key], "engine_inputs")
        dup = df.duplicated([agent_key, period_key]).sum()
        if dup:
            raise ValueError(f"engine_inputs has {dup} duplicated rows by ({agent_key}, {period_key}).")

    # Basic numeric check for columns declared in config metric definitions
    metric_defs = (config.get("metric_catalog") or {}).get("metrics", {})
    for table_name, df in normalized.items():
        # If table has "metric" / "value" long-form, validate that value is numeric
        if {"metric", "value"}.issubset(df.columns):
            bad = pd.to_numeric(df["value"], errors="coerce").isna() & df["value"].notna()
            if bad.any():
                n = int(bad.sum())
                raise ValueError(f"Table '{table_name}' has {n} non-numeric values in 'value'.")

        # If wide-form, validate configured metrics that exist in that table
        for m_name, m_def in metric_defs.items():
            col = m_def.get("column", m_name)
            if col in df.columns:
                coerced = pd.to_numeric(df[col], errors="coerce")
                if coerced.isna().all():
                    raise ValueError(f"Table '{table_name}' metric '{col}' cannot be parsed as numeric.")
