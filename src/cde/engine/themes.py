# src/cde/engine/themes.py
"""
Tier 2 of the selection model: coaching THEMES.

A theme (configs/mappings/themes.yaml) groups related metrics. When >= a
configured fraction of a theme's members are *deficient* for an agent, the
theme "qualifies" and can be delivered instead of a single behavior.

Deficiency here is deliberately LOOSER than the solo-coaching bar:
  * Evidence gating already happened upstream (apply_signal_thresholds ->
    eligible_signals -> aggregate_scores_window). A metric that reaches
    ``scores_windowed`` is evidence-valid.
  * A member counts as deficient when its direction-aware ``score_level``
    (percentile deficit vs benchmark, computed in scoring/assemble.py) clears
    a single low global floor ``theme_selection.score_level_floor``.
  * The solo path additionally applies ``eligibility.min_confidence`` and the
    versioned business weights + argmax; themes apply none of those, so a
    metric "not worth coaching alone" can still count toward a pattern.

This module reads ``scores_windowed`` (the 8-week decision grain). It does NOT
need the ICP_Client cohort (that is only used by Tier-1 break-glass), so the
windowed frame — which does not carry ``icp_client`` — is the right input.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pandas as pd

from cde.utils.logging import get_logger

log = get_logger(__name__)

_KEYS = ["agent_id", "period", "call_type"]

_CANDIDATE_COLS = [
    "agent_id", "period", "call_type",
    "theme", "conversation_type",
    "theme_score", "n_members", "n_deficient",
    "members", "deficient_metrics",
]

_MEMBER_DETAIL_COLS = [
    "agent_id", "period", "call_type", "theme",
    "metric", "value", "benchmark", "gap",
    "level_score", "trend_score", "risk_score", "confidence_score",
    "deficient",
]


def _load_themes(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Return {theme_name: {"members": [...], "conversation_type": str}} from
    config["themes"], tolerating either the wrapped ({"themes": {...}}) or the
    already-unwrapped shape (mirrors prioritization.apply._load_topic_map).
    """
    raw = config.get("themes") or {}
    if not isinstance(raw, dict):
        return {}
    inner = raw.get("themes", raw)
    if not isinstance(inner, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for name, spec in inner.items():
        if not isinstance(spec, dict):
            continue
        members = [str(m) for m in (spec.get("members") or [])]
        if not members:
            continue
        out[str(name)] = {
            "members": members,
            "conversation_type": spec.get("conversation_type"),
        }
    return out


def _theme_selection_cfg(config: Dict[str, Any]) -> Tuple[float, float, str]:
    ts = config.get("theme_selection") or {}
    frac = float(ts.get("count_fraction", 0.5))
    floor = float(ts.get("score_level_floor", 0.15))
    aggregate = str(ts.get("aggregate", "mean")).lower().strip()
    if aggregate not in ("mean", "sum"):
        aggregate = "mean"
    return frac, floor, aggregate


def _conversation_type_for_theme(theme_spec: Dict[str, Any], config: Dict[str, Any]) -> str:
    ct = theme_spec.get("conversation_type")
    if ct:
        return str(ct)
    return ((config.get("conversation_types") or {}).get("default")) or "Performance Coaching"


def _empty_candidates() -> pd.DataFrame:
    return pd.DataFrame(columns=_CANDIDATE_COLS)


def _empty_member_detail() -> pd.DataFrame:
    return pd.DataFrame(columns=_MEMBER_DETAIL_COLS)


def build_theme_candidates(
    scores_windowed: pd.DataFrame,
    config: Dict[str, Any],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Build qualifying theme candidates per (agent_id, period, call_type).

    Returns (theme_candidates, theme_members_detail):
      - theme_candidates: one row per QUALIFYING (agent, period, call_type, theme),
        with theme_score (aggregate of deficient members' score_total), n_members,
        n_deficient, and the member/deficient metric lists.
      - theme_members_detail: one row per (agent, period, call_type, theme, member)
        for every theme that appears in candidates — used to build receipt drivers.

    Empty, well-formed frames are returned when no themes are configured or no
    theme qualifies (so the caller falls through to single-behavior selection).
    """
    themes = _load_themes(config)
    if not themes or scores_windowed is None or scores_windowed.empty:
        return _empty_candidates(), _empty_member_detail()

    frac, floor, aggregate = _theme_selection_cfg(config)

    sw = scores_windowed.copy()
    for c in _KEYS + ["metric", "score_level", "score_total"]:
        if c not in sw.columns:
            raise ValueError(
                f"build_theme_candidates: scores_windowed missing required column '{c}'. "
                f"cols={sw.columns.tolist()}"
            )

    # Reconstruct evidence (value/benchmark/gap) the same way build_topic_candidates does,
    # so theme receipts mirror single-behavior receipts.
    sw["benchmark"] = pd.to_numeric(sw.get("benchmark_8w"), errors="coerce")
    sw["gap"] = pd.to_numeric(sw.get("level_8w"), errors="coerce")
    sw["value"] = sw["benchmark"] + sw["gap"]
    sw["level_score"] = pd.to_numeric(sw["score_level"], errors="coerce").fillna(0.0)
    sw["trend_score"] = pd.to_numeric(sw.get("score_trend"), errors="coerce").fillna(0.0)
    sw["risk_score"] = pd.to_numeric(sw.get("score_risk"), errors="coerce").fillna(0.0)
    sw["confidence_score"] = pd.to_numeric(sw.get("score_confidence"), errors="coerce").fillna(0.0)
    sw["_total"] = pd.to_numeric(sw["score_total"], errors="coerce").fillna(0.0)
    sw["deficient"] = sw["level_score"] >= floor

    available_metrics = set(sw["metric"].unique())

    # metric -> [themes it belongs to]  (a metric may be in multiple themes)
    metric_to_themes: Dict[str, List[str]] = {}
    for name, spec in themes.items():
        missing = [m for m in spec["members"] if m not in available_metrics]
        if missing:
            log.info(
                "themes: theme %r has %d member metric(s) not present in scores_windowed "
                "(they count as non-deficient): %s",
                name, len(missing), missing,
            )
        for m in spec["members"]:
            metric_to_themes.setdefault(m, []).append(name)

    # Long frame: one row per (score row) x (theme the metric belongs to)
    sw_themed = sw[sw["metric"].isin(metric_to_themes)].copy()
    if sw_themed.empty:
        return _empty_candidates(), _empty_member_detail()
    sw_themed["theme"] = sw_themed["metric"].map(metric_to_themes)
    sw_themed = sw_themed.explode("theme", ignore_index=True)

    # Member detail (kept for receipts; only deficient members are drivers).
    member_detail = sw_themed[
        _KEYS + ["theme", "metric", "value", "benchmark", "gap",
                 "level_score", "trend_score", "risk_score", "confidence_score", "deficient"]
    ].copy()

    # Aggregate per (agent, period, call_type, theme).
    grp_keys = _KEYS + ["theme"]
    def _agg(g: pd.DataFrame) -> pd.Series:
        deficient = g[g["deficient"]]
        n_def = int(len(deficient))
        if aggregate == "sum":
            score = float(deficient["_total"].sum())
        else:
            score = float(deficient["_total"].mean()) if n_def else 0.0
        return pd.Series({
            "n_deficient": n_def,
            "theme_score": score,
            "deficient_metrics": sorted(deficient["metric"].tolist()),
        })

    agg = sw_themed.groupby(grp_keys, dropna=False, sort=True).apply(_agg, include_groups=False).reset_index()

    # n_members is the CONFIGURED theme size (missing members count against qualification).
    theme_size = {name: len(spec["members"]) for name, spec in themes.items()}
    theme_members = {name: sorted(spec["members"]) for name, spec in themes.items()}
    agg["n_members"] = agg["theme"].map(theme_size).astype(int)
    agg["members"] = agg["theme"].map(theme_members)
    agg["conversation_type"] = agg["theme"].map(
        lambda t: _conversation_type_for_theme(themes.get(t, {}), config)
    )

    # Qualify: >= frac of configured members deficient, and at least one deficient.
    qualifies = (agg["n_deficient"] > 0) & (
        agg["n_deficient"] >= (frac * agg["n_members"] - 1e-9)
    )
    candidates = agg[qualifies].copy()
    if candidates.empty:
        return _empty_candidates(), _empty_member_detail()

    candidates = candidates[_CANDIDATE_COLS].reset_index(drop=True)

    # Restrict member detail to themes that actually qualified for that agent.
    q_index = candidates[grp_keys].drop_duplicates()
    member_detail = member_detail.merge(q_index, on=grp_keys, how="inner").reset_index(drop=True)
    member_detail = member_detail[_MEMBER_DETAIL_COLS]

    return candidates, member_detail


def top_theme_per_agent(theme_candidates: pd.DataFrame) -> pd.DataFrame:
    """
    Deterministically pick the single best qualifying theme per (agent, period,
    call_type): highest theme_score, then theme name ascending for stability.
    """
    if theme_candidates is None or theme_candidates.empty:
        return _empty_candidates()
    df = theme_candidates.sort_values(
        ["theme_score", "theme"], ascending=[False, True], kind="mergesort"
    )
    top = df.groupby(_KEYS, as_index=False, sort=True).head(1).copy()
    return top.reset_index(drop=True)
