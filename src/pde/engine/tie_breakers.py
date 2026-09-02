from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd


def deterministic_sort(candidates: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """
    Deterministic ordering. No randomness. No "latest model".

    Sort precedence:
      1) priority_score desc
      2) risk_score desc
      3) confidence_score desc
      4) stable lexicographic topic order (or configured priority order)
    """
    df = candidates.copy()

    # Optional explicit topic order
    order = (config.get("tie_breakers") or {}).get("topic_order")
    if order:
        rank = {t: i for i, t in enumerate(order)}
        df["_topic_rank"] = df["topic"].map(rank).fillna(len(rank)).astype(int)
        df = df.sort_values(
            ["priority_score", "risk_score", "confidence_score", "_topic_rank", "topic"],
            ascending=[False, False, False, True, True],
        ).drop(columns=["_topic_rank"])
    else:
        df = df.sort_values(
            ["priority_score", "risk_score", "confidence_score", "topic"],
            ascending=[False, False, False, True],
        )
    return df
