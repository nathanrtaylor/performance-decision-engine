"""Per-agent dampening evidence: WHY each candidate topic was dampened.

Dampening (src/cde/prioritization/dampening.py) halves the priority_score of a candidate topic
the agent was coached on within the last ``dampening.periods`` weeks. The dampened flag alone
(on topic_candidates.csv) does not say which coaching triggered it, and the decision receipts
carry no dampening fields. This module reconstructs the evidence chain:

    dampened (agent, topic) candidate  ->  the coaching event(s) that triggered it

by joining the dampened candidates to the behavior-level coaching events
(``ingestion.coaching_history.map_coaching_events`` — same crosswalk / count_status / count_types
filters that drive dampening) within the same weekly-period window the pipeline uses.

Produces one row per dampened (agent_id, period, call_type, topic) with the candidate evidence,
the pre/post-dampening priority, and the triggering coaching behaviors + dates. Wired into the
pipeline (writes dampening_evidence.csv) and reused by tools/export_dampening_evidence.py.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from cde.ingestion.coaching_history import map_coaching_events
from cde.utils.ids import normalize_agent_id
from cde.utils.logging import get_logger

log = get_logger(__name__)

_OUT_COLS = [
    "agent_id", "period", "call_type", "topic", "metric",
    "value", "benchmark", "gap", "level_score",
    "priority_score_original", "priority_score_dampened", "multiplier", "mode", "periods_weeks",
    "n_coaching_events", "last_coached_period", "weeks_since_last",
    "coaching_behaviors", "coaching_dates",
]


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=_OUT_COLS)


def build_dampening_evidence(
    candidates: pd.DataFrame,
    coaching_history_raw: Optional[pd.DataFrame],
    config: Dict[str, Any],
) -> pd.DataFrame:
    """
    Return one row per dampened (agent_id, period, call_type, topic) explaining why it was
    dampened: the candidate's value/benchmark/gap/level_score, the pre/post-dampening priority,
    and the triggering coaching events (count, last period, weeks-since, behaviors, dates).

    ``candidates`` is the post-dampening topic-candidate frame (must carry the ``dampened`` flag).
    ``coaching_history_raw`` is the normalized raw coaching-history table (None => no evidence).
    Returns a well-formed empty frame when there is nothing to explain.
    """
    if candidates is None or candidates.empty or "dampened" not in candidates.columns:
        return _empty()

    d = candidates[candidates["dampened"] == True].copy()  # noqa: E712
    if d.empty:
        return _empty()

    events = map_coaching_events(coaching_history_raw, config)
    if events is None or events.empty:
        # Dampened but no reconstructable coaching (e.g. id-format drift) — surface, don't crash.
        log.warning("dampening_evidence: %d dampened candidate(s) but no mappable coaching events.", len(d))
        return _empty()

    damp = config.get("dampening") or {}
    mode = str(damp.get("mode", "multiply")).lower()
    periods = int(damp.get("periods", 2))
    multiplier = float(damp.get("multiplier", 0.5))

    d["_aid"] = normalize_agent_id(d["agent_id"])
    d["period"] = pd.to_datetime(d["period"], errors="coerce")
    ev = events.rename(columns={"agent_id": "_aid"})

    # Join each dampened candidate to its triggering coaching events, then window on coach_period
    # (the weekly bucket the pipeline dampens on).
    j = d.merge(ev, on=["_aid", "topic"], how="left")
    weeks_since = (j["period"] - j["coach_period"]).dt.days / 7.0
    in_window = j["coach_period"].notna() & weeks_since.between(0, periods)
    j = j[in_window].copy()
    if j.empty:
        log.warning("dampening_evidence: no coaching events fell in the %d-wk window for dampened candidates.", periods)
        return _empty()
    j["weeks_since"] = ((j["period"] - j["coach_period"]).dt.days / 7.0).round(2)

    key = ["agent_id", "period", "call_type", "topic", "metric",
           "value", "benchmark", "gap", "level_score", "priority_score"]

    def _agg(g: pd.DataFrame) -> pd.Series:
        g = g.sort_values("coach_period")
        return pd.Series({
            "n_coaching_events": int(len(g)),
            "last_coached_period": g["coach_period"].max().date().isoformat(),
            "weeks_since_last": float(g["weeks_since"].min()),
            "coaching_behaviors": "; ".join(sorted(g["behavior"].astype(str).unique())),
            "coaching_dates": "; ".join(sorted(dt.date().isoformat() for dt in g["coaching_date"].dropna())),
        })

    out = j.groupby(key, dropna=False, sort=True).apply(_agg, include_groups=False).reset_index()

    out = out.rename(columns={"priority_score": "priority_score_dampened"})
    out["multiplier"] = multiplier
    out["mode"] = mode
    out["periods_weeks"] = periods
    # multiply mode leaves the halved score in candidates; reconstruct the pre-dampen value.
    out["priority_score_original"] = (
        out["priority_score_dampened"] / multiplier
        if (mode == "multiply" and multiplier)
        else out["priority_score_dampened"]
    )

    for c in _OUT_COLS:
        if c not in out.columns:
            out[c] = pd.NA
    out = out[_OUT_COLS].sort_values(["priority_score_original", "agent_id"], ascending=[False, True])
    return out.reset_index(drop=True)
