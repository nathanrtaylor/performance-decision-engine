from __future__ import annotations

from typing import Any, Dict, Tuple

import pandas as pd


def topic_distribution(recommendations: pd.DataFrame) -> pd.DataFrame:
    return (
        recommendations.groupby(["call_type", "topic"], as_index=False)
        .size()
        .rename(columns={"size": "n"})
        .sort_values(["call_type", "n"], ascending=[True, False])
    )


def expected_lift_stub(recommendations: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """
    Placeholder: attach lift ranges per topic from config.
    Config example:
      lift_assumptions:
        "Reduce Transfer Rate": {min: 0.01, max: 0.03, metric: "transfer_rate"}
    """
    lift = (config.get("lift_assumptions") or {})
    rows = []
    for _, r in recommendations.iterrows():
        topic = r["topic"]
        a = lift.get(topic, {})
        rows.append(
            {
                "agent_id": r["agent_id"],
                "period": r["period"],
                "call_type": r["call_type"],
                "topic": topic,
                "lift_min": float(a.get("min", 0.0)),
                "lift_max": float(a.get("max", 0.0)),
                "lift_metric": a.get("metric"),
            }
        )
    return pd.DataFrame(rows)
