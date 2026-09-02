from __future__ import annotations

from typing import List, Optional

import pandas as pd


def _missing_cols(df: pd.DataFrame, required: List[str]) -> List[str]:
    return [c for c in required if c not in df.columns]


def validate_agent_metrics(df: pd.DataFrame) -> None:
    required = ["agent_id", "period", "metric", "numerator", "denominator", "calc"]
    missing = _missing_cols(df, required)
    if missing:
        raise ValueError(f"agent_metrics missing required columns: {missing}")

    # Optional: enforce uniqueness if you expect it
    key = ["agent_id", "period", "metric"]
    if df.duplicated(subset=key).any():
        raise ValueError(f"agent_metrics has duplicate keys on {key}")


def validate_behavior_scores(df: pd.DataFrame) -> None:
    required = ["agent_id", "period", "behavior", "score"]
    missing = _missing_cols(df, required)
    if missing:
        raise ValueError(f"behavior_scores missing required columns: {missing}")