# src/cde/engine/select.py
"""
Three-tier selection orchestrator producing exactly ONE recommendation per agent.

Precedence per (agent_id, period, call_type):
  1. break-glass single (Tier 1)  -> engine.break_glass
  2. qualifying theme    (Tier 2)  -> engine.themes
  3. ordinary single     (Tier 3)  -> engine.recommend.recommend_for_population  (UNCHANGED)

Backward compatibility: with no themes.yaml configured and no metric carrying a
break_glass flag, Tiers 1 and 2 are empty and every agent falls through to the
Tier-3 single. The returned recs then equal recommend_for_population's output
plus an additive ``tier`` column, so pre-theme behavior is preserved.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

import pandas as pd

from cde.engine.break_glass import detect_break_glass, top_break_glass_per_agent
from cde.engine.recommend import recommend_for_population
from cde.engine.themes import build_theme_candidates, top_theme_per_agent

_KEYS = ["agent_id", "period", "call_type"]


def _key_index(df: pd.DataFrame) -> pd.DataFrame:
    return df[_KEYS].drop_duplicates()


def _anti_join(df: pd.DataFrame, taken: pd.DataFrame) -> pd.DataFrame:
    """Rows of df whose (agent, period, call_type) key is NOT in taken."""
    if df.empty:
        return df
    if taken is None or taken.empty:
        return df
    merged = df.merge(_key_index(taken).assign(_taken=1), on=_KEYS, how="left")
    return merged[merged["_taken"].isna()].drop(columns=["_taken"])


def _break_glass_to_recs(bg_top: pd.DataFrame) -> pd.DataFrame:
    if bg_top is None or bg_top.empty:
        return pd.DataFrame()
    out = pd.DataFrame({
        "agent_id": bg_top["agent_id"],
        "period": bg_top["period"],
        "call_type": bg_top["call_type"],
        "topic": bg_top["topic"],
        "conversation_type": bg_top["conversation_type"],
        "priority_score": bg_top["severity"].astype(float),
        "metric": bg_top["metric"],
        "value": bg_top["value"],
        "benchmark": bg_top["benchmark"],
        "gap": bg_top["gap"],
        "tier": "break_glass",
        "cohort_pct": bg_top["cohort_pct"],
    })
    return out


def _theme_to_recs(theme_top: pd.DataFrame) -> pd.DataFrame:
    if theme_top is None or theme_top.empty:
        return pd.DataFrame()
    out = pd.DataFrame({
        "agent_id": theme_top["agent_id"],
        "period": theme_top["period"],
        "call_type": theme_top["call_type"],
        "topic": theme_top["theme"],           # theme name occupies the topic slot
        "conversation_type": theme_top["conversation_type"],
        "priority_score": theme_top["theme_score"].astype(float),
        "tier": "theme",
        "n_members": theme_top["n_members"],
        "n_deficient": theme_top["n_deficient"],
    })
    return out


def select_recommendations(
    candidates: pd.DataFrame,
    eligible_signals: pd.DataFrame,
    scores_windowed: pd.DataFrame,
    config: Dict[str, Any],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Return (recs, selection_detail).

    recs: one row per (agent_id, period, call_type) with a ``tier`` column
      (break_glass | theme | single) plus the columns recommend_for_population
      already emits for singles (topic, conversation_type, priority_score,
      metric/value/benchmark/gap, *_score, weights).

    selection_detail: a long frame used by receipts. For themes it carries one
      row per (agent, theme, member) with driver evidence; for break-glass one
      row per override with cohort_pct. Columns:
        kind, agent_id, period, call_type, theme, metric, value, benchmark, gap,
        level_score, trend_score, risk_score, confidence_score, deficient, cohort_pct
    """
    # Tier 3 (unchanged) — computed for the whole population; also the fallback.
    singles = recommend_for_population(candidates, config)
    singles = singles.copy()
    singles["tier"] = "single"

    # Tier 1
    bg_all = detect_break_glass(eligible_signals, config)
    bg_top = top_break_glass_per_agent(bg_all)

    # Tier 2
    theme_cands, theme_members = build_theme_candidates(scores_windowed, config)
    theme_top = top_theme_per_agent(theme_cands)
    # Themes never override a break-glass agent.
    theme_top = _anti_join(theme_top, bg_top)

    # Singles only where neither Tier 1 nor Tier 2 claimed the agent.
    claimed = pd.concat([_key_index(bg_top), _key_index(theme_top)], ignore_index=True) \
        if (not bg_top.empty or not theme_top.empty) else pd.DataFrame(columns=_KEYS)
    singles_kept = _anti_join(singles, claimed)

    recs = pd.concat(
        [_break_glass_to_recs(bg_top), _theme_to_recs(theme_top), singles_kept],
        ignore_index=True,
    )
    recs = recs.sort_values(_KEYS, kind="mergesort").reset_index(drop=True)

    selection_detail = _build_selection_detail(theme_top, theme_members, bg_top)
    return recs, selection_detail


def _build_selection_detail(
    theme_top: pd.DataFrame,
    theme_members: pd.DataFrame,
    bg_top: pd.DataFrame,
) -> pd.DataFrame:
    cols = [
        "kind", "agent_id", "period", "call_type", "theme", "metric",
        "value", "benchmark", "gap",
        "level_score", "trend_score", "risk_score", "confidence_score",
        "deficient", "cohort_pct",
    ]
    frames = []

    if theme_top is not None and not theme_top.empty and theme_members is not None and not theme_members.empty:
        chosen = theme_top[_KEYS + ["theme"]].drop_duplicates()
        det = theme_members.merge(chosen, on=_KEYS + ["theme"], how="inner").copy()
        det["kind"] = "theme"
        det["cohort_pct"] = pd.NA
        frames.append(det)

    if bg_top is not None and not bg_top.empty:
        bg = bg_top.copy()
        bg["kind"] = "break_glass"
        bg["theme"] = pd.NA
        for c in ["level_score", "trend_score", "risk_score", "confidence_score"]:
            bg[c] = pd.NA
        bg["deficient"] = True
        frames.append(bg)

    if not frames:
        return pd.DataFrame(columns=cols)

    out = pd.concat(frames, ignore_index=True)
    for c in cols:
        if c not in out.columns:
            out[c] = pd.NA
    return out[cols]
