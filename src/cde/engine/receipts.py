# src/cde/engine/receipts.py

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import pandas as pd

from cde.explainability.core_metrics import build_core_metrics_index
from cde.explainability.evidence import build_competitors
from cde.utils.metric_format import display_map, format_value
from cde.explainability.templates import (
    narrative_why_this, narrative_why_now, narrative_why_not,
    narrative_theme_why_this, narrative_theme_why_now, narrative_theme_why_not,
    narrative_break_glass, narrative_break_glass_why_now,
    narrative_abstention, _is_missing, _above_benchmark,
    narrative_reinforcement_why_this, narrative_reinforcement_why_now,
    narrative_reinforcement_why_not,
)
from cde.utils.io import _json_default

_TREND_FIELDS = ["trend_8w", "recency_shift", "weeks_present", "direction"]

_KEYS = ["agent_id", "period", "call_type"]


def _period_str(p: Any) -> Optional[str]:
    """Format a period as YYYY-MM-DD (the week-ending date shown in receipts)."""
    if p is None or (isinstance(p, float) and pd.isna(p)):
        return None
    try:
        return pd.Timestamp(p).strftime("%Y-%m-%d")
    except Exception:
        return str(p)


def _periods_equal(a: Any, b: Any) -> bool:
    try:
        return pd.Timestamp(a).normalize() == pd.Timestamp(b).normalize()
    except Exception:
        return a == b


def _core_metrics_block(
    core_metrics_idx: Optional[Dict[Any, Any]],
    agent_id: Any,
    call_type: Any,
    rec_period: Any,
) -> Dict[str, Any]:
    """Assemble the receipt's core-metrics block, keyed by ``(agent_id, call_type)``.

    The block is anchored on the agent's latest *eligible* week, which can lag the
    recommendation's week when the agent had no qualifying calls in the current week.
    We surface the block's own ``period`` and ``as_of_latest`` (whether that week is the
    recommendation week) so the dashboard states which week the numbers are from and
    calls it out when they are older, instead of silently hiding the block.
    """
    block = (core_metrics_idx or {}).get((agent_id, call_type))
    if not block or not block.get("metrics"):
        return {"period": None, "as_of_latest": True, "metrics": []}
    data_period = block["period"]
    return {
        "period": _period_str(data_period),
        "as_of_latest": _periods_equal(data_period, rec_period),
        "metrics": block["metrics"],
    }


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

    # Per-metric trend/recency lookup, built once from scores_windowed (already passed in as
    # ``scores``). Used to narrate the trend in "why now" without widening the rec keep-lists.
    trend_idx = _trend_index(scores)

    # Per-expert "core metrics this week" block (config-driven, per icp_client). Built once
    # from eligible_signals (``signals``) + the trend index; attached to every receipt so it
    # flows to both decision_receipts.jsonl and the expert-dashboard modals.
    core_metrics_idx = build_core_metrics_index(signals, trend_idx, config)

    # How many topic candidates each agent had this period (a count of 1 means the chosen
    # behavior was the only one with enough data — used to explain "sole-signal" recs).
    cand_counts = _candidate_counts(candidates)

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
            "advisory": False,  # set True for reinforcement (expert already at/above benchmark)
            "excluded_signals": excluded_for_agent,
            "core_metrics": _core_metrics_block(core_metrics_idx, agent_id, call_type, period),
            "provenance": provenance,
            "config_hash": config_hash,
        }

        if tier == "theme":
            receipts.append(_theme_receipt(base, r, selection_detail, trend_idx))
        elif tier == "break_glass":
            receipts.append(_break_glass_receipt(base, r, trend_idx))
        else:
            n_candidates = cand_counts.get((agent_id, period, call_type), 1)
            receipts.append(_single_receipt(base, r, comps, trend_idx, n_candidates))

    # Abstention receipts (explicit, explained non-recommendations)
    if has_abstentions:
        for _, a in abstentions.iterrows():
            receipts.append(_abstention_receipt(a, provenance, config_hash, core_metrics_idx))

    # Presentation only: attach per-metric display strings (value/benchmark/gap) alongside the raw
    # numbers on every per-metric record, per configs/mappings/metric_catalog.yaml `display`.
    dmap = display_map(config)
    if dmap:
        for rec in receipts:
            _decorate_receipt_display(rec, dmap)

    return pd.DataFrame(receipts)


def _decorate_metric_record(d: Dict[str, Any], dmap: Dict[str, Any]) -> None:
    """Add value_display / benchmark_display / gap_display to a per-metric record (in place)."""
    if not isinstance(d, dict):
        return
    spec = dmap.get(d.get("metric"))
    if not spec:
        return
    for raw_key, disp_key in (("value", "value_display"),
                              ("benchmark", "benchmark_display"),
                              ("gap", "gap_display")):
        if d.get(raw_key) is not None:
            s = format_value(d.get(raw_key), spec)
            if s is not None:
                d[disp_key] = s


def _decorate_receipt_display(rec: Dict[str, Any], dmap: Dict[str, Any]) -> None:
    """Decorate every per-metric record in a receipt (drivers, core_metrics, excluded_signals)."""
    for d in rec.get("drivers") or []:
        _decorate_metric_record(d, dmap)
    cm = rec.get("core_metrics")
    if isinstance(cm, dict):
        for d in cm.get("metrics") or []:
            _decorate_metric_record(d, dmap)
    for x in rec.get("excluded_signals") or []:
        _decorate_metric_record(x, dmap)


def _trend_index(scores: Optional[pd.DataFrame]) -> Dict[Any, Dict[str, Any]]:
    """Map (agent_id, period, call_type, metric) -> {trend_8w, recency_shift, weeks_present, direction}."""
    if scores is None or scores.empty or "metric" not in scores.columns:
        return {}
    have = [c for c in _TREND_FIELDS if c in scores.columns]
    if not have:
        return {}
    idx: Dict[Any, Dict[str, Any]] = {}
    for _, s in scores[_KEYS + ["metric"] + have].iterrows():
        key = (s["agent_id"], s["period"], s.get("call_type"), s.get("metric"))
        idx[key] = {c: s.get(c) for c in have}
    return idx


def _trend_for(idx: Dict[Any, Dict[str, Any]], agent_id, period, call_type, metric) -> Dict[str, Any]:
    return idx.get((agent_id, period, call_type, metric), {})


def _candidate_counts(candidates: Optional[pd.DataFrame]) -> Dict[Any, int]:
    """Number of topic candidates per (agent_id, period, call_type)."""
    if candidates is None or candidates.empty or not set(_KEYS).issubset(candidates.columns):
        return {}
    g = candidates.groupby(_KEYS).size()
    return {k: int(v) for k, v in g.items()}


def _excluded_for(excluded_signals, agent_id, period, call_type):
    if excluded_signals is None or excluded_signals.empty:
        return []
    ex = excluded_signals[
        (excluded_signals["agent_id"] == agent_id)
        & (excluded_signals["period"] == period)
        & (excluded_signals["call_type"] == call_type)
    ]
    return ex.to_dict(orient="records") if not ex.empty else []


def _single_receipt(base: Dict[str, Any], r: pd.Series, comps: pd.DataFrame, trend_idx: Dict[Any, Dict[str, Any]], n_candidates: int = 1) -> Dict[str, Any]:
    if comps is not None and not comps.empty:
        comp_rows = comps[
            (comps["agent_id"] == base["agent_id"])
            & (comps["period"] == base["period"])
            & (comps["call_type"] == base["call_type"])
        ]
        competitors = comp_rows.to_dict(orient="records") if not comp_rows.empty else []
    else:
        competitors = []

    trend = _trend_for(trend_idx, base["agent_id"], base["period"], base["call_type"], r.get("metric"))
    direction = trend.get("direction")
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
        "trend_8w": _float_or_none(trend.get("trend_8w")),
        "recency_shift": _float_or_none(trend.get("recency_shift")),
        "direction": direction,
    }

    # Reinforcement case: the expert is already at/above benchmark on the chosen behavior, so
    # this is a strength to reinforce, not a gap to close. Detected direction-aware from the gap.
    above = _above_benchmark(r.get("gap"), direction)
    n_excluded = len(base.get("excluded_signals") or [])
    sole_signal = int(n_candidates or 1) <= 1

    if above:
        narrative = {
            "why_this": narrative_reinforcement_why_this(r),
            "why_now": narrative_reinforcement_why_now(
                r, trend_8w=trend.get("trend_8w"), recency_shift=trend.get("recency_shift"),
                direction=direction, n_excluded=n_excluded, sole_signal=sole_signal,
            ),
            "why_not_others": narrative_reinforcement_why_not(competitors, n_excluded, sole_signal),
        }
    else:
        narrative = {
            "why_this": narrative_why_this(r),
            "why_now": narrative_why_now(
                r, trend_8w=trend.get("trend_8w"), recency_shift=trend.get("recency_shift"),
                direction=direction,
            ),
            "why_not_others": narrative_why_not(competitors),
        }

    return {
        **base,
        "advisory": bool(above),
        "kind": "reinforcement" if above else "deficit",
        "drivers": [driver],
        "competing_topics": competitors,
        "narrative": narrative,
    }


def _theme_receipt(base: Dict[str, Any], r: pd.Series, selection_detail: Optional[pd.DataFrame], trend_idx: Dict[Any, Dict[str, Any]]) -> Dict[str, Any]:
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
            trend = _trend_for(trend_idx, base["agent_id"], base["period"], base["call_type"], d.get("metric"))
            drivers.append({
                "metric": d.get("metric"),
                "value": _float_or_none(d.get("value")),
                "benchmark": _float_or_none(d.get("benchmark")),
                "gap": _float_or_none(d.get("gap")),
                "level_score": _float_or_none(d.get("level_score"), default=0.0),
                "trend_score": _float_or_none(d.get("trend_score"), default=0.0),
                "risk_score": _float_or_none(d.get("risk_score"), default=0.0),
                "confidence_score": _float_or_none(d.get("confidence_score"), default=0.0),
                "trend_8w": _float_or_none(trend.get("trend_8w")),
                "recency_shift": _float_or_none(trend.get("recency_shift")),
                "direction": trend.get("direction"),
            })

    n_deficient = int(r.get("n_deficient") or len(drivers))
    n_members = int(r.get("n_members") or len(drivers))

    # The single-behavior rec this theme displaced (attached in select._theme_to_recs).
    alt_topic = r.get("alt_topic")
    alt_metric = r.get("alt_metric")
    competing_topics = []
    if not _is_missing(alt_topic):
        competing_topics = [{
            "topic": alt_topic,
            "metric": None if _is_missing(alt_metric) else alt_metric,
            "gap": _float_or_none(r.get("alt_gap")),
            "level_score": _float_or_none(r.get("alt_level_score"), default=0.0),
            "reason_not_selected": "a coaching theme spanning several related behaviors was higher-leverage",
        }]

    return {
        **base,
        "drivers": drivers,
        "theme_membership": {
            "n_deficient": n_deficient,
            "n_members": n_members,
            "deficient_metrics": [d["metric"] for d in drivers],
        },
        "competing_topics": competing_topics,
        "narrative": {
            "why_this": narrative_theme_why_this(theme, drivers, n_deficient, n_members),
            "why_now": narrative_theme_why_now(drivers),
            "why_not_others": narrative_theme_why_not(theme, n_deficient, n_members, alt_topic, alt_metric),
        },
    }


def _abstention_receipt(
    a: pd.Series,
    provenance: Dict[str, Any],
    config_hash: Optional[str] = None,
    core_metrics_idx: Optional[Dict[Any, Any]] = None,
) -> Dict[str, Any]:
    reason = a.get("reason")
    best_topic = a.get("best_topic")
    best_ps = _float_or_none(a.get("best_priority_score"))
    best_lvl = _float_or_none(a.get("best_level_score"))
    drivers = []
    if best_topic is not None and not (isinstance(best_topic, float) and pd.isna(best_topic)):
        drivers = [{"topic": best_topic, "priority_score": best_ps, "level_score": best_lvl}]
    core_metrics = _core_metrics_block(
        core_metrics_idx, a.get("agent_id"), a.get("call_type"), a.get("period")
    )
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
        "core_metrics": core_metrics,
        "narrative": {
            "why_this": narrative_abstention(reason, best_topic, best_ps),
            "why_now": "Withheld this cycle; re-evaluated each run as data updates.",
            "why_not_others": "No topic cleared the coaching floor / evidence gates.",
        },
        "provenance": provenance,
        "config_hash": config_hash,
    }


def _break_glass_receipt(base: Dict[str, Any], r: pd.Series, trend_idx: Dict[Any, Dict[str, Any]]) -> Dict[str, Any]:
    cohort_pct = r.get("cohort_pct")
    trend = _trend_for(trend_idx, base["agent_id"], base["period"], base["call_type"], r.get("metric"))
    driver = {
        "metric": r.get("metric"),
        "value": _float_or_none(r.get("value")),
        "benchmark": _float_or_none(r.get("benchmark")),
        "gap": _float_or_none(r.get("gap")),
        "cohort_pct": _float_or_none(cohort_pct),
        "trend_8w": _float_or_none(trend.get("trend_8w")),
        "recency_shift": _float_or_none(trend.get("recency_shift")),
    }
    return {
        **base,
        "override": True,
        "reason": "break_glass",
        "drivers": [driver],
        "competing_topics": [],
        "narrative": {
            "why_this": narrative_break_glass(r),
            "why_now": narrative_break_glass_why_now(
                r,
                trend_8w=trend.get("trend_8w"),
                recency_shift=trend.get("recency_shift"),
                direction=trend.get("direction"),
            ),
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
    # pandas NA checks (float NaN, pd.NA, NaT) — obj is scalar here (dict/list handled above)
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    # numpy / pandas scalar -> python scalar
    try:
        if hasattr(obj, "item"):
            return obj.item()
    except Exception:
        pass
    return obj
