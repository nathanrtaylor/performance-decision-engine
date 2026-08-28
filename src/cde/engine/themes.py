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

The qualification fraction is global by default (``theme_selection.count_fraction``)
but a theme may override it with its own ``count_fraction`` in themes.yaml. This is
the intended lever for a WIDE theme of correlated metrics (e.g. Call Control's four
efficiency metrics): raising just that theme to 0.75 (3-of-4) stops a marginal 2-of-4
pattern from crowding out other themes and single behaviors, without touching the bar
for smaller themes — a global bump would instead demand 3-of-3 on any 3-member theme
(integer rounding of 0.75*3=2.25) and gut the thin-tailed ones.

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
    "theme", "display_topic", "conversation_type",
    "theme_score", "n_members", "n_deficient",
    "members", "deficient_metrics",
]

_MEMBER_DETAIL_COLS = [
    "agent_id", "period", "call_type", "theme",
    "metric", "value", "benchmark", "gap",
    "level_score", "trend_score", "risk_score", "confidence_score",
    "deficient",
]


def _normalize_cohorts(raw: Any) -> set | None:
    """
    Normalize a theme's optional ``cohorts`` allow-list to a set of lowercase,
    stripped cohort labels — or None when absent/empty (=> theme applies to ALL
    cohorts, today's behavior). The labels are matched EXACTLY against the derived
    ``icp_client`` (which already carries composite ``icp_client::client`` splits,
    see ingestion.normalize.derive_cohort), so we lowercase/strip both sides.
    """
    if not raw:
        return None
    if isinstance(raw, str):  # tolerate a single string instead of a list
        raw = [raw]
    out = {str(c).strip().lower() for c in raw if str(c).strip()}
    return out or None


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
            # Optional per-theme qualification bar; None => use the global
            # theme_selection.count_fraction. Resolved (validated) in
            # build_theme_candidates via _resolve_count_fraction.
            "count_fraction": spec.get("count_fraction"),
            # Optional per-theme cohort allow-list; None => applies to ALL cohorts.
            # Applied in build_theme_candidates once the agent->cohort map is known.
            "cohorts": _normalize_cohorts(spec.get("cohorts")),
            # Optional display label; None => the theme name is used as the topic.
            # Multiple themes may share a `topic` to present as one coaching label
            # while being computed separately (pair with `cohorts` for per-cohort variants).
            "topic": spec.get("topic"),
        }
    return out


def _resolve_count_fraction(name: str, spec: Dict[str, Any], default: float) -> float:
    """
    Per-theme count_fraction override, falling back to the global default.

    A theme may set its own ``count_fraction`` in themes.yaml to make its
    qualification bar stricter or looser than the global one — e.g. a wide
    theme of correlated metrics can require 3-of-4 (0.75) so a marginal 2-of-4
    pattern no longer crowds out other themes and single behaviors. Invalid or
    out-of-range values fall back to the global default with a warning.
    """
    raw = spec.get("count_fraction")
    if raw is None:
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        log.warning(
            "themes: theme %r has non-numeric count_fraction %r; using global %.3f",
            name, raw, default,
        )
        return default
    if not (0.0 < val <= 1.0):
        log.warning(
            "themes: theme %r count_fraction %.3f out of range (0, 1]; using global %.3f",
            name, val, default,
        )
        return default
    return val


def _theme_selection_cfg(config: Dict[str, Any]) -> Tuple[float, float, str]:
    # The theme tier's selection knobs are centralized in themes.yaml, next to
    # the theme definitions: cfg["themes"]["theme_selection"]. Fall back to a
    # top-level cfg["theme_selection"] for backward compatibility (older
    # active.yaml layout and programmatic callers that pass it directly).
    themes_map = config.get("themes")
    ts = themes_map.get("theme_selection") if isinstance(themes_map, dict) else None
    if ts is None:
        ts = config.get("theme_selection")
    ts = ts or {}
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
    agent_cohort: Dict[str, str] | None = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Build qualifying theme candidates per (agent_id, period, call_type).

    Returns (theme_candidates, theme_members_detail):
      - theme_candidates: one row per QUALIFYING (agent, period, call_type, theme),
        with theme_score (aggregate of deficient members' score_total), n_members,
        n_deficient, and the member/deficient metric lists.
      - theme_members_detail: one row per (agent, period, call_type, theme, member)
        for every theme that appears in candidates — used to build receipt drivers.

    ``agent_cohort`` (agent_id -> lowercase cohort label) enables the per-theme
    ``cohorts`` allow-list: a theme that declares ``cohorts`` only qualifies for
    agents whose cohort is in that set. scores_windowed does not carry icp_client
    (by design), so select_recommendations derives this map from eligible_signals.
    When None, cohort scoping cannot be enforced (unit-test callers) and a theme's
    ``cohorts`` restriction is skipped with a warning. Themes without ``cohorts``
    always apply to every cohort.

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

    # Per-theme cohort allow-list: drop (agent x theme) rows whose cohort is not
    # in a scoped theme's `cohorts` set. Themes with no `cohorts` (falsy) apply to
    # every cohort. Applied BEFORE member_detail so both outputs respect it.
    theme_cohorts = {name: spec.get("cohorts") for name, spec in themes.items()}
    if any(theme_cohorts.values()):
        if agent_cohort is not None:
            coh = sw_themed["agent_id"].map(agent_cohort)
            keep = [
                (not theme_cohorts.get(t)) or (c in theme_cohorts[t])
                for t, c in zip(sw_themed["theme"], coh)
            ]
            sw_themed = sw_themed[keep].copy()
            if sw_themed.empty:
                return _empty_candidates(), _empty_member_detail()
        else:
            log.warning(
                "themes: %d theme(s) declare `cohorts` but no agent_cohort map was "
                "provided; cohort scoping not applied.",
                sum(1 for v in theme_cohorts.values() if v),
            )

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
    # Per-theme qualification bar: explicit spec.count_fraction, else global frac.
    theme_frac = {name: _resolve_count_fraction(name, spec, frac) for name, spec in themes.items()}
    # Display label: explicit spec.topic, else the internal theme name. Pure passthrough —
    # aggregation stays keyed on the internal theme name so same-labeled themes compute apart.
    theme_topic = {name: (spec.get("topic") or name) for name, spec in themes.items()}
    agg["n_members"] = agg["theme"].map(theme_size).astype(int)
    agg["members"] = agg["theme"].map(theme_members)
    agg["count_fraction"] = agg["theme"].map(theme_frac).astype(float)
    agg["display_topic"] = agg["theme"].map(theme_topic)
    agg["conversation_type"] = agg["theme"].map(
        lambda t: _conversation_type_for_theme(themes.get(t, {}), config)
    )

    # Qualify: >= (per-theme) frac of configured members deficient, and at least one deficient.
    qualifies = (agg["n_deficient"] > 0) & (
        agg["n_deficient"] >= (agg["count_fraction"] * agg["n_members"] - 1e-9)
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
