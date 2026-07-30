from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from cde.utils.ids import normalize_agent_id
from cde.utils.logging import get_logger

log = get_logger(__name__)


def apply_recent_coaching_dampening(
    candidates: pd.DataFrame,
    config: Dict[str, Any],
    history: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Dampen topics coached recently to prevent coaching whiplash. Deterministic.

    A candidate topic is "recently coached" when the same (agent_id, topic) was coached within
    the last ``dampening.periods`` weeks of the candidate's decision period (window_end):

        0 <= (period - last_coached_period) in weeks <= periods

    Modes:
      - "multiply" (default): scale priority_score by ``dampening.multiplier`` (topic stays in
        contention, so a severe gap can still win).
      - "suppress": drop the recently-coached candidate rows entirely.

    Always adds a boolean ``dampened`` column. No-ops (returns candidates unchanged, with
    ``dampened=False``) when ``history`` is None/empty or required columns are missing.

    Expected ``history`` schema: agent_id, topic, last_coached_period.
    """
    df = candidates.copy()
    df["dampened"] = False

    damp = config.get("dampening") or {}
    mode = str(damp.get("mode", "multiply")).lower()
    periods = int(damp.get("periods", 2))
    multiplier = float(damp.get("multiplier", 0.5))

    if history is None or getattr(history, "empty", True):
        return df

    required = {"agent_id", "topic", "period"}
    if not required.issubset(df.columns):
        log.warning("dampening: candidates missing one of %s; skipping.", required)
        return df
    if not {"agent_id", "topic", "last_coached_period"}.issubset(history.columns):
        log.warning("dampening: history missing required columns; skipping.")
        return df

    hist = history.copy()

    # Normalize join keys on both sides so id-format drift doesn't silently prevent matches.
    df["_aid"] = normalize_agent_id(df["agent_id"])
    hist["_aid"] = normalize_agent_id(hist["agent_id"])
    hist = hist.rename(columns={"topic": "_topic"})
    hist["last_coached_period"] = pd.to_datetime(hist["last_coached_period"], errors="coerce")
    # keep the most recent coaching per (agent, topic) in case history isn't pre-reduced
    hist = (
        hist.sort_values("last_coached_period")
        .drop_duplicates(subset=["_aid", "_topic"], keep="last")
    )

    joined = df.merge(
        hist[["_aid", "_topic", "last_coached_period"]],
        left_on=["_aid", "topic"],
        right_on=["_aid", "_topic"],
        how="left",
    )

    period = pd.to_datetime(joined["period"], errors="coerce")
    last = pd.to_datetime(joined["last_coached_period"], errors="coerce")
    weeks_since = (period - last).dt.days / 7.0
    recent = last.notna() & (weeks_since >= 0) & (weeks_since <= periods)
    recent = recent.fillna(False)

    joined["dampened"] = recent

    if mode == "suppress":
        result = joined[~recent].copy()
    else:  # multiply (soft)
        if "priority_score" in joined.columns:
            joined.loc[recent, "priority_score"] = (
                pd.to_numeric(joined.loc[recent, "priority_score"], errors="coerce") * multiplier
            )
        result = joined

    n_dampened = int(recent.sum())
    if n_dampened:
        log.info("dampening: %s candidate(s) dampened (mode=%s, periods=%s).", n_dampened, mode, periods)

    return result.drop(columns=["_aid", "_topic", "last_coached_period"], errors="ignore")
