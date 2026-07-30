# src/cde/prioritization/apply.py

from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from cde.prioritization.weights import get_metric_weight, get_topic_weight, _unwrap_root
from cde.prioritization.eligibility import apply_eligibility


def _prioritization_flags(config: Dict[str, Any]) -> tuple[bool, Dict[str, bool]]:
    """
    Read governance flags from the metric_catalog:
      - disallow_prioritization_if_not_flagged (governance)
      - eligible_for_prioritization (per metric)
    Returns (enforce, {metric: eligible}).
    """
    mc = _unwrap_root(config.get("metric_catalog") or {}, "metric_catalog")
    gov = mc.get("governance") or {}
    enforce = bool(gov.get("disallow_prioritization_if_not_flagged", False))
    metrics = mc.get("metrics") or {}
    flags = {m: bool((meta or {}).get("eligible_for_prioritization", True)) for m, meta in metrics.items()}
    return enforce, flags


def _load_topic_map(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Expects config["topic_map"] to be the loaded YAML from mappings/topic_map.yaml:

      topic_map:
        metric_to_topic: {...}
        topic_to_conversation_type: {...}
        allowed_topics_by_call_type: {...}

    Returns the inner dict under the 'topic_map' root (or {}).
    """
    tm = config.get("topic_map") or {}
    # handle either shape:
    # 1) {"topic_map": {...}} (recommended)
    # 2) {...} (if you load inner dict directly)
    return tm.get("topic_map", tm) if isinstance(tm, dict) else {}


def _require_cols(df: pd.DataFrame, cols: list[str], name: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{name} missing required columns {missing}. cols={df.columns.tolist()}")


def _canonicalize_key_cols(df: pd.DataFrame) -> pd.DataFrame:
    """
    Defensive normalization for common naming drift and merge suffixing.
    """
    ren: Dict[str, str] = {}

    # metric aliasing
    if "metric" not in df.columns:
        if "signal" in df.columns:
            ren["signal"] = "metric"
        elif "metric_x" in df.columns:
            ren["metric_x"] = "metric"
        elif "metric_y" in df.columns:
            ren["metric_y"] = "metric"

    # call_type aliasing
    if "call_type" not in df.columns:
        if "call_type_x" in df.columns:
            ren["call_type_x"] = "call_type"
        elif "call_type_y" in df.columns:
            ren["call_type_y"] = "call_type"

    # period aliasing
    if "period" not in df.columns:
        if "week_ending" in df.columns:
            ren["week_ending"] = "period"

    # agent aliasing
    if "agent_id" not in df.columns:
        if "expert_id" in df.columns:
            ren["expert_id"] = "agent_id"

    return df.rename(columns=ren) if ren else df


def build_topic_candidates(signals: pd.DataFrame, scores: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """
    Produces candidate topics with explicit scoring components and a deterministic
    'priority_score' used ONLY for selection / ranking.

    - merges score table with key signal evidence (value/benchmark/gap/direction when available)
    - maps metric -> topic using mappings/topic_map.yaml
    - applies metric/topic weights + linear priority model
    - aggregates to topic-level candidates with "top metric driver" evidence
    - applies eligibility rules
    """
    tm = _load_topic_map(config)
    metric_to_topic = tm.get("metric_to_topic") or {}
    allowed_topics_by_ct = tm.get("allowed_topics_by_call_type") or {}

    allow_unmapped = bool((config.get("topic_map_options") or {}).get("allow_unmapped_metrics", False))

    # Canonicalize column names defensively
    scores = _canonicalize_key_cols(scores.copy())
    signals = _canonicalize_key_cols(signals.copy())

    _require_cols(scores, ["agent_id", "period", "call_type", "metric"], "scores")
    _require_cols(scores, ["score_total"], "scores")  # build_signals should output score_total
    _require_cols(signals, ["agent_id", "period", "call_type", "metric"], "signals")

    # Evidence (value/benchmark/gap) shown for the recommendation comes from the 8-WEEK WINDOW
    # aggregates, which are populated for every scored agent. A point-in-time join on window_end
    # instead leaves blanks for any agent missing that exact final week (common with weekly
    # reporting gaps), even though the decision itself is window-based. Window-average evidence is
    # both always-present and faithful to how the score was computed.
    if {"level_8w", "benchmark_8w"}.issubset(scores.columns):
        df = scores.copy()
        df["benchmark"] = pd.to_numeric(df["benchmark_8w"], errors="coerce")
        df["gap"] = pd.to_numeric(df["level_8w"], errors="coerce")     # mean signed gap over window
        df["value"] = df["benchmark"] + df["gap"]                       # implied mean value
        if "direction" not in df.columns:
            df["direction"] = None
    else:
        # Legacy fallback: point-in-time evidence join (used when window aggregates aren't present).
        evidence_cols = ["value", "benchmark", "gap", "direction"]
        available_evidence = [c for c in evidence_cols if c in signals.columns]
        join_cols = ["agent_id", "period", "call_type", "metric"]
        if available_evidence:
            df = scores.merge(signals[join_cols + available_evidence], on=join_cols, how="left")
        else:
            df = scores.copy()

    # Backward/forward compatible score column naming
    rename_map = {
        "score_level": "level_score",
        "score_trend": "trend_score",
        "score_risk": "risk_score",
        "score_confidence": "confidence_score",
        "score_total": "total_score",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    _require_cols(df, ["metric", "call_type", "total_score"], "candidates_base")

    # Governance: only metrics flagged eligible_for_prioritization may drive recommendations
    # (metric_catalog.governance.disallow_prioritization_if_not_flagged). Quality behaviors are
    # off by default until Ops approves them.
    enforce_flags, elig_flags = _prioritization_flags(config)
    if enforce_flags:
        df = df[df["metric"].map(lambda m: elig_flags.get(m, False))].copy()
        if df.empty:
            return pd.DataFrame(
                columns=[
                    "agent_id", "period", "call_type", "topic", "priority_score",
                    "level_score", "trend_score", "risk_score", "confidence_score",
                    "metric", "metric_weight", "topic_weight",
                ]
            )

    # Map canonical metric -> topic (drop if unmapped unless explicitly allowed)
    df["topic"] = df["metric"].map(metric_to_topic)
    if allow_unmapped:
        df["topic"] = df["topic"].fillna(df["metric"])
    else:
        # If metric_to_topic is empty/missing, this will drop everything; fail loudly instead.
        if not metric_to_topic:
            raise ValueError(
                "topic_map.metric_to_topic is empty or missing and allow_unmapped_metrics=false. "
                "You are dropping all metrics. Fix mappings/topic_map.yaml or set allow_unmapped_metrics=true."
            )
        df = df[df["topic"].notna()].copy()

    # Optional semantic constraints from topic_map.yaml
    if allowed_topics_by_ct and "call_type" in df.columns:
        allowed_sets = {ct: set(topics) for ct, topics in allowed_topics_by_ct.items()}
        keep_mask = []
        for ct, topic in zip(df["call_type"].tolist(), df["topic"].tolist()):
            if ct in allowed_sets:
                keep_mask.append(topic in allowed_sets[ct])
            else:
                keep_mask.append(True)
        df = df[pd.Series(keep_mask, index=df.index)].copy()

    # If everything got filtered out, return an empty-but-well-formed frame.
    if df.empty:
        return pd.DataFrame(
            columns=[
                "agent_id",
                "period",
                "call_type",
                "topic",
                "priority_score",
                "level_score",
                "trend_score",
                "risk_score",
                "confidence_score",
                "metric",
                "metric_weight",
                "topic_weight",
            ]
        )

    # Priority score = the already-composed multi-axis score_total (scoring/assemble.py) scaled by
    # versioned business weights. Composition lives in ONE place; here we only apply governance
    # emphasis (category/metric weight, optional topic weight). No re-weighting of the axes.
    df["metric_weight"] = [
        float(get_metric_weight(m, ct, config)) for m, ct in zip(df["metric"].tolist(), df["call_type"].tolist())
    ]
    df["topic_weight"] = [
        float(get_topic_weight(t, ct, config)) for t, ct in zip(df["topic"].tolist(), df["call_type"].tolist())
    ]

    base = pd.to_numeric(df.get("total_score", pd.Series(0.0, index=df.index)), errors="coerce").fillna(0.0)
    df["priority_score"] = (base * df["metric_weight"] * df["topic_weight"]).astype(float)

    # Aggregate to topic-level candidates per agent/period/call_type
    # Keep the "top metric driver" as evidence
    df = df.sort_values(
        ["agent_id", "period", "call_type", "topic", "priority_score"],
        ascending=[True, True, True, True, False],
        kind="mergesort",  # stable
    )
    top_driver = df.groupby(["agent_id", "period", "call_type", "topic"]).head(1).copy()

    agg = df.groupby(["agent_id", "period", "call_type", "topic"], as_index=False).agg(
        priority_score=("priority_score", "max"),
        level_score=("level_score", "max"),
        trend_score=("trend_score", "max"),
        risk_score=("risk_score", "max"),
        confidence_score=("confidence_score", "min"),  # conservative
    )

    # Keep evidence columns if present
    driver_cols = [
        "agent_id",
        "period",
        "call_type",
        "topic",
        "metric",
        "metric_weight",
        "topic_weight",
    ]
    for c in ["value", "benchmark", "gap", "direction"]:
        if c in top_driver.columns:
            driver_cols.append(c)

    out = agg.merge(
        top_driver[driver_cols],
        on=["agent_id", "period", "call_type", "topic"],
        how="left",
        suffixes=("", "_driver"),
    )

    out = apply_eligibility(out, config)
    return out