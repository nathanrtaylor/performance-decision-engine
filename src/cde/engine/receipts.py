# src/cde/engine/receipts.py

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import pandas as pd

from cde.explainability.evidence import build_competitors
from cde.explainability.templates import (
    narrative_why_this, narrative_why_now, narrative_why_not,
    narrative_theme_why_this, narrative_theme_why_now, narrative_break_glass,
    narrative_abstention,
)
from cde.utils.io import _json_default

_KEYS = ["agent_id", "period", "call_type"]


def build_receipts(
    recommendations: pd.DataFrame,
    candidates: pd.DataFrame,
    signals: Optional[pd.DataFrame],
    scores: Optional[pd.DataFrame],
    config: Dict[str, Any],
    excluded_signals: Optional[pd.DataFrame] = None,
    selection_detail: Optional[pd.DataFrame] = None,
    abstentions: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Build structured "decision receipts" for each recommendation.

    Receipts answer:
      - why this topic/theme (drivers + weights + benchmark/gap)
      - why now (risk/trend/confidence)
      - why not others (top competitors)
      - why some signals did not qualify (excluded_signals with reason codes)
      - provenance (config version, data snapshot, engine version)

    Recommendations may be one of three tiers (``tier`` column):
      - ``single``      : today's single-behavior rec (single driver + competitors).
      - ``theme``       : a coaching theme (multiple member drivers from selection_detail).
      - ``break_glass`` : a critical single override (single driver + override flag).
    Rows with no ``tier`` are treated as ``single`` (backward compatible).
    """
    has_recs = recommendations is not None and not recommendations.empty
    has_abstentions = abstentions is not None and not abstentions.empty
    if not has_recs and not has_abstentions:
        return pd.DataFrame([])

    recs = recommendations.copy() if has_recs else pd.DataFrame(columns=["agent_id", "period", "call_type", "topic", "tier"])
    if "tier" not in recs.columns:
        recs["tier"] = "single"

    # Competitors are only meaningful for single-tier topic recs.
    single_recs = recs[recs["tier"] == "single"]
    comps = build_competitors(single_recs, candidates, config) if not single_recs.empty else pd.DataFrame()

    meta = config.get("meta") or {}
    provenance = {
        "config_version": meta.get("version"),
        "data_snapshot": meta.get("data_snapshot"),
        "engine_version": meta.get("engine_version", "0.1.0"),
    }
    # Content hash of the resolved config, stamped top-level for per-decision traceability.
    config_hash = meta.get("config_hash")

    receipts = []
    for _, r in recs.iterrows():
        agent_id = r["agent_id"]
        period = r["period"]
        call_type = r.get("call_type")
        tier = r.get("tier", "single")

        excluded_for_agent = _excluded_for(excluded_signals, agent_id, period, call_type)

        base = {
            "agent_id": agent_id,
            "period": period,
            "call_type": call_type,
            "recommended_topic": r["topic"],
            "conversation_type": r.get("conversation_type"),
            "priority_score": _float_or_none(r.get("priority_score"), default=0.0),
            "tier": tier,
            "excluded_signals": excluded_for_agent,
            "provenance": provenance,
            "config_hash": config_hash,
        }

        if tier == "theme":
            receipts.append(_theme_receipt(base, r, selection_detail))
        elif tier == "break_glass":
            receipts.append(_break_glass_receipt(base, r, selection_detail))
        else:
            receipts.append(_single_receipt(base, r, comps))

    # Abstention receipts (explicit, explained non-recommendations)
    if has_abstentions:
        for _, a in abstentions.iterrows():
            receipts.append(_abstention_receipt(a, provenance, config_hash))

    return pd.DataFrame(receipts)


def _excluded_for(excluded_signals, agent_id, period, call_type):
    if excluded_signals is None or excluded_signals.empty:
        return []
    ex = excluded_signals[
        (excluded_signals["agent_id"] == agent_id)
        & (excluded_signals["period"] == period)
        & (excluded_signals["call_type"] == call_type)
    ]
    return ex.to_dict(orient="records") if not ex.empty else []


def _single_receipt(base: Dict[str, Any], r: pd.Series, comps: pd.DataFrame) -> Dict[str, Any]:
    if comps is not None and not comps.empty:
        comp_rows = comps[
            (comps["agent_id"] == base["agent_id"])
            & (comps["period"] == base["period"])
            & (comps["call_type"] == base["call_type"])
        ]
        competitors = comp_rows.to_dict(orient="records") if not comp_rows.empty else []
    else:
        competitors = []

    driver = {
        "metric": r.get("metric"),
        "value": _float_or_none(r.get("value")),
        "benchmark": _float_or_none(r.get("benchmark")),
        "gap": _float_or_none(r.get("gap")),
        "level_score": _float_or_none(r.get("level_score"), default=0.0),
        "trend_score": _float_or_none(r.get("trend_score"), default=0.0),
        "risk_score": _float_or_none(r.get("risk_score"), default=0.0),
        "confidence_score": _float_or_none(r.get("confidence_score"), default=0.0),
        "metric_weight": _float_or_none(r.get("metric_weight"), default=0.0),
        "topic_weight": _float_or_none(r.get("topic_weight"), default=0.0),
    }
    return {
        **base,
        "drivers": [driver],
        "competing_topics": competitors,
        "narrative": {
            "why_this": narrative_why_this(r),
            "why_now": narrative_why_now(r),
            "why_not_others": narrative_why_not(competitors),
        },
    }


def _theme_receipt(base: Dict[str, Any], r: pd.Series, selection_detail: Optional[pd.DataFrame]) -> Dict[str, Any]:
    theme = base["recommended_topic"]
    drivers = []
    if selection_detail is not None and not selection_detail.empty:
        det = selection_detail[
            (selection_detail.get("kind") == "theme")
            & (selection_detail["agent_id"] == base["agent_id"])
            & (selection_detail["period"] == base["period"])
            & (selection_detail["call_type"] == base["call_type"])
            & (selection_detail["theme"] == theme)
            & (selection_detail["deficient"] == True)  # noqa: E712
        ].sort_values("level_score", ascending=False)
        for _, d in det.iterrows():
            drivers.append({
                "metric": d.get("metric"),
                "value": _float_or_none(d.get("value")),
                "benchmark": _float_or_none(d.get("benchmark")),
                "gap": _float_or_none(d.get("gap")),
                "level_score": _float_or_none(d.get("level_score"), default=0.0),
                "trend_score": _float_or_none(d.get("trend_score"), default=0.0),
                "risk_score": _float_or_none(d.get("risk_score"), default=0.0),
                "confidence_score": _float_or_none(d.get("confidence_score"), default=0.0),
            })

    n_deficient = int(r.get("n_deficient") or len(drivers))
    n_members = int(r.get("n_members") or len(drivers))
    return {
        **base,
        "drivers": drivers,
        "theme_membership": {
            "n_deficient": n_deficient,
            "n_members": n_members,
            "deficient_metrics": [d["metric"] for d in drivers],
        },
        "competing_topics": [],
        "narrative": {
            "why_this": narrative_theme_why_this(theme, drivers, n_deficient, n_members),
            "why_now": narrative_theme_why_now(drivers),
            "why_not_others": "A coaching theme was preferred over any single-behavior alternative.",
        },
    }


def _abstention_receipt(a: pd.Series, provenance: Dict[str, Any], config_hash: Optional[str] = None) -> Dict[str, Any]:
    reason = a.get("reason")
    best_topic = a.get("best_topic")
    best_ps = _float_or_none(a.get("best_priority_score"))
    best_lvl = _float_or_none(a.get("best_level_score"))
    drivers = []
    if best_topic is not None and not (isinstance(best_topic, float) and pd.isna(best_topic)):
        drivers = [{"topic": best_topic, "priority_score": best_ps, "level_score": best_lvl}]
    return {
        "agent_id": a.get("agent_id"),
        "period": a.get("period"),
        "call_type": a.get("call_type"),
        "recommended_topic": None,
        "conversation_type": None,
        "priority_score": None,
        "tier": "abstained",
        "reason": reason,
        "drivers": drivers,
        "competing_topics": [],
        "excluded_signals": [],
        "narrative": {
            "why_this": narrative_abstention(reason, best_topic, best_ps),
            "why_now": "Withheld this cycle; re-evaluated each run as data updates.",
            "why_not_others": "No topic cleared the coaching floor / evidence gates.",
        },
        "provenance": provenance,
        "config_hash": config_hash,
    }


def _break_glass_receipt(base: Dict[str, Any], r: pd.Series, selection_detail: Optional[pd.DataFrame]) -> Dict[str, Any]:
    cohort_pct = r.get("cohort_pct")
    driver = {
        "metric": r.get("metric"),
        "value": _float_or_none(r.get("value")),
        "benchmark": _float_or_none(r.get("benchmark")),
        "gap": _float_or_none(r.get("gap")),
        "cohort_pct": _float_or_none(cohort_pct),
    }
    return {
        **base,
        "override": True,
        "reason": "break_glass",
        "drivers": [driver],
        "competing_topics": [],
        "narrative": {
            "why_this": narrative_break_glass(r),
            "why_now": "A flagged critical metric is both severely and recently deficient.",
            "why_not_others": "Break-glass override supersedes theme and single-behavior selection.",
        },
    }


def receipts_to_jsonl(receipts: pd.DataFrame) -> str:
    """
    Serialize receipts to JSONL.
    Each row is a JSON object (one receipt per line).
    """
    lines = []
    for _, row in receipts.iterrows():
        obj = row.to_dict()

        # Ensure pandas/numpy types don't break JSON serialization
        obj = _json_safe(obj)

        lines.append(json.dumps(obj, ensure_ascii=False, default=_json_default))
    return "\n".join(lines) + "\n"


def _float_or_none(x: Any, default: Optional[float] = None) -> Optional[float]:
    if x is None:
        return default
    try:
        if isinstance(x, float) and pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def _json_safe(obj: Any) -> Any:
    """
    Convert nested structures containing pandas/numpy scalars to JSON-safe python types.
    """
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    # pandas NA checks
    try:
        if isinstance(obj, float) and pd.isna(obj):
            return None
    except Exception:
        pass
    # numpy / pandas scalar -> python scalar
    try:
        if hasattr(obj, "item"):
            return obj.item()
    except Exception:
        pass
    return obj
