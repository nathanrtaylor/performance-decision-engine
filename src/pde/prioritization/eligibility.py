from __future__ import annotations

from typing import Any, Dict

import pandas as pd


def apply_eligibility(candidates: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """
    Hard filters / rules:
      - allowed topics by call_type
      - minimum confidence
      - optional metric presence

    Config example:
      eligibility:
        min_confidence: 0.4
        allowed_topics_by_call_type:
          claims: ["Reduce Transfer Rate", "Resolution Rate"]
    """
    df = candidates.copy()
    el = config.get("eligibility") or {}

    min_conf = float(el.get("min_confidence", 0.0))
    if "confidence_score" in df.columns:
        df = df[df["confidence_score"].fillna(0.0) >= min_conf]

    allowed = el.get("allowed_topics_by_call_type") or {}
    if allowed and "call_type" in df.columns and "topic" in df.columns:
        mask = []
        for ct, topic in zip(df["call_type"].tolist(), df["topic"].tolist()):
            if ct in allowed:
                mask.append(topic in set(allowed[ct]))
            else:
                mask.append(True)
        df = df[pd.Series(mask, index=df.index)]

    return df
