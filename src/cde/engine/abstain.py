# src/cde/engine/abstain.py
"""
Abstention: withhold a recommendation when coaching is not warranted, and record WHY.

Two reasons an agent ends a run with no recommendation:
  - ``below_coaching_floor`` — a single-behavior recommendation was produced, but its ``priority_score``
    is below ``abstention.min_priority_score`` (the agent is performing adequately). Break-glass and
    theme recommendations are material by construction and are NEVER abstained.
  - ``no_qualified_signal`` — the agent is in the coachable universe but produced no recommendation at
    all (every signal was gated out, or none had a trustworthy reference/confidence).

The result partitions the coachable universe: every agent ends in exactly one of ``recs_kept`` or
``abstentions``. Abstentions are surfaced explicitly (abstentions.csv, decision_receipts tier
"abstained", and the run dashboard) so a withheld recommendation is a visible, explained decision —
not a silent gap.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import pandas as pd

from cde.utils.ids import normalize_agent_id

_KEYS = ["agent_id", "period", "call_type"]

ABSTENTION_COLS = [
    "agent_id", "period", "call_type", "reason",
    "best_topic", "best_priority_score", "best_level_score",
]

REASON_BELOW_FLOOR = "below_coaching_floor"
REASON_NO_SIGNAL = "no_qualified_signal"


def _empty_abstentions() -> pd.DataFrame:
    return pd.DataFrame(columns=ABSTENTION_COLS)


def _agent_universe(agents: Optional[pd.DataFrame]) -> set:
    if agents is None or getattr(agents, "empty", True) or "agent_id" not in agents.columns:
        return set()
    return set(normalize_agent_id(agents["agent_id"]).dropna().unique())


def _decision_context(recs: pd.DataFrame, candidates: pd.DataFrame) -> Tuple[Any, Any]:
    """A single (period, call_type) to stamp on no_qualified_signal rows."""
    for frame in (recs, candidates):
        if frame is not None and not frame.empty:
            period = frame["period"].iloc[0] if "period" in frame.columns else None
            call_type = frame["call_type"].iloc[0] if "call_type" in frame.columns else None
            return period, call_type
    return None, None


def apply_abstention(
    recs: pd.DataFrame,
    agents: Optional[pd.DataFrame],
    candidates: Optional[pd.DataFrame],
    config: Dict[str, Any],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Split ``recs`` into (recs_kept, abstentions). No-op (returns recs, empty) when abstention is
    disabled. Break-glass and theme tiers are always kept.
    """
    ab_cfg = config.get("abstention") or {}
    if not ab_cfg.get("enabled", False) or recs is None or recs.empty:
        return (recs if recs is not None else pd.DataFrame()), _empty_abstentions()

    floor = float(ab_cfg.get("min_priority_score", 0.0))
    df = recs.copy()
    if "tier" not in df.columns:
        df["tier"] = "single"

    # --- Reason 1: single-tier rec below the materiality floor ---
    ps = pd.to_numeric(df.get("priority_score"), errors="coerce").fillna(0.0)
    below = (df["tier"] == "single") & (ps < floor)

    recs_kept = df[~below].copy()

    below_rows = df[below]
    below_abs = pd.DataFrame({
        "agent_id": below_rows["agent_id"].values,
        "period": below_rows["period"].values,
        "call_type": below_rows["call_type"].values,
        "reason": REASON_BELOW_FLOOR,
        "best_topic": below_rows["topic"].values,
        "best_priority_score": pd.to_numeric(below_rows.get("priority_score"), errors="coerce").values,
        "best_level_score": pd.to_numeric(below_rows.get("level_score"), errors="coerce").values
        if "level_score" in below_rows.columns else pd.NA,
    }) if not below_rows.empty else _empty_abstentions()

    # --- Reason 2: coachable-universe agents with no recommendation at all ---
    universe = _agent_universe(agents)
    no_signal_abs = _empty_abstentions()
    if universe:
        # Agents the selector produced SOME rec for (including below-floor) are already accounted for.
        covered = set(normalize_agent_id(df["agent_id"]).dropna().unique())
        missing = sorted(universe - covered)
        if missing:
            period, call_type = _decision_context(recs, candidates)
            best = _best_available(missing, candidates)
            no_signal_abs = pd.DataFrame({
                "agent_id": missing,
                "period": period,
                "call_type": call_type,
                "reason": REASON_NO_SIGNAL,
                "best_topic": [best.get(a, (None, None, None))[0] for a in missing],
                "best_priority_score": [best.get(a, (None, None, None))[1] for a in missing],
                "best_level_score": [best.get(a, (None, None, None))[2] for a in missing],
            })

    abstentions = pd.concat([below_abs, no_signal_abs], ignore_index=True)
    if not abstentions.empty:
        abstentions = abstentions[ABSTENTION_COLS].reset_index(drop=True)
    return recs_kept.reset_index(drop=True), abstentions


def _best_available(agent_ids: list, candidates: Optional[pd.DataFrame]) -> Dict[str, tuple]:
    """For each agent, the highest-priority candidate topic (if any survived to candidates)."""
    if candidates is None or candidates.empty or "agent_id" not in candidates.columns:
        return {}
    c = candidates.copy()
    c["_aid"] = normalize_agent_id(c["agent_id"])
    c = c[c["_aid"].isin(set(agent_ids))]
    if c.empty:
        return {}
    c["_ps"] = pd.to_numeric(c.get("priority_score"), errors="coerce").fillna(0.0)
    c = c.sort_values("_ps", ascending=False, kind="mergesort")
    top = c.groupby("_aid", as_index=False).head(1)
    out: Dict[str, tuple] = {}
    for _, r in top.iterrows():
        out[r["_aid"]] = (
            r.get("topic"),
            float(r["_ps"]),
            float(pd.to_numeric(r.get("level_score"), errors="coerce")) if "level_score" in top.columns and pd.notna(r.get("level_score")) else None,
        )
    return out
