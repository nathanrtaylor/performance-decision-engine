from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

import pandas as pd

from cde.engine.tie_breakers import deterministic_sort


@dataclass(frozen=True)
class Recommendation:
    agent_id: str
    period: str
    call_type: str
    topic: str
    conversation_type: str
    priority_score: float


def _unwrap_topic_map_block(config: Dict[str, Any]) -> Dict[str, Any]:
    tm = config.get("topic_map") or {}
    if not isinstance(tm, dict):
        return {}
    inner = tm.get("topic_map")
    return inner if isinstance(inner, dict) else tm


def _conversation_type_for(topic: str, config: Dict[str, Any]) -> str:
    """
    Deterministic mapping: active conversation_types.by_topic overrides topic_map defaults,
    then topic_map.topic_to_conversation_type, then conversation_types.default.
    """
    ct = config.get("conversation_types") or {}
    by_topic_active = ct.get("by_topic") or {}
    if topic in by_topic_active:
        return by_topic_active[topic]

    tm = _unwrap_topic_map_block(config)
    tmap = tm.get("topic_to_conversation_type") or {}
    if isinstance(tmap, dict) and topic in tmap:
        return tmap[topic]

    return ct.get("default", "Performance Coaching")


def recommend_for_population(topic_candidates: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """
    Deterministic selection:
      - rank candidates per agent+period+call_type
      - select top 1
    """
    required = {"agent_id", "period", "call_type", "topic", "priority_score"}
    missing = required - set(topic_candidates.columns)
    if missing:
        raise ValueError(f"Candidates missing required columns: {sorted(missing)}")

    df = deterministic_sort(topic_candidates, config)
    top = df.groupby(["agent_id", "period", "call_type"], as_index=False).head(1).copy()

    top["conversation_type"] = top["topic"].apply(lambda t: _conversation_type_for(t, config))

    # Keep only essentials + evidence pointers
    cols = [
        "agent_id", "period", "call_type",
        "topic", "conversation_type",
        "priority_score",
        "metric", "value", "benchmark", "gap",
        "level_score", "trend_score", "risk_score", "confidence_score",
        "metric_weight", "topic_weight",
    ]
    keep = [c for c in cols if c in top.columns]
    return top[keep].reset_index(drop=True)
