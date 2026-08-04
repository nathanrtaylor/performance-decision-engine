from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from cde.utils.config import unwrap_root as _unwrap
from cde.utils.ids import normalize_agent_id
from cde.utils.logging import get_logger

log = get_logger(__name__)


def build_coaching_history(
    normalized: Dict[str, pd.DataFrame], config: Dict[str, Any]
) -> Optional[pd.DataFrame]:
    """
    Collapse raw coaching events into the dampening input grain:

        agent_id | topic | last_coached_period

    Uses the governed crosswalk (configs/mappings/coaching_history_map.yaml) to map each event's
    ``behavior_selected`` to an engine topic. Unmapped behaviors are logged and dropped (they
    simply do not dampen). Returns None when no ``coaching_history`` table is present, so the
    pipeline degrades gracefully to no dampening.
    """
    raw = normalized.get("coaching_history")
    if raw is None or getattr(raw, "empty", True):
        return None

    xmap = _unwrap(config.get("coaching_history_map") or {}, "coaching_history_map")
    map_key = xmap.get("map_key", "behavior_selected")
    count_status = set(xmap.get("count_status") or [])
    behavior_to_topic = xmap.get("behavior_to_topic") or {}

    df = raw.copy()

    # 1) keep only counted coaching statuses (defensive; also filtered at extraction)
    if count_status and "coaching_status" in df.columns:
        df = df[df["coaching_status"].astype(str).str.strip().isin(count_status)]
    if df.empty:
        return None

    if map_key not in df.columns:
        log.warning(
            "coaching_history: map_key '%s' not found in columns %s; no dampening applied.",
            map_key, list(df.columns),
        )
        return None

    # 2) map behavior -> topic (case-insensitive; log unmapped; they don't dampen)
    norm_map = {str(k).strip().casefold(): v for k, v in behavior_to_topic.items()}
    subject = df[map_key].astype(str).str.strip()
    df["topic"] = subject.str.casefold().map(norm_map)
    unmapped = sorted(subject[df["topic"].isna()].dropna().unique().tolist())
    if unmapped:
        log.warning(
            "coaching_history: %d unmapped %s value(s) will not dampen; extend "
            "configs/mappings/coaching_history_map.yaml: %s",
            len(unmapped), map_key, unmapped,
        )
    df = df[df["topic"].notna()].copy()
    if df.empty:
        return None

    # 3) canonical agent_id + period, then reduce to last coached period per (agent, topic)
    df["agent_id"] = normalize_agent_id(df["agent_id"])

    period_col = "period" if "period" in df.columns else ("coaching_date" if "coaching_date" in df.columns else None)
    if period_col is None:
        log.warning("coaching_history: no 'period'/'coaching_date' column; no dampening applied.")
        return None

    df["last_coached_period"] = pd.to_datetime(df[period_col], errors="coerce")
    df = df[df["agent_id"].notna() & df["last_coached_period"].notna()]
    if df.empty:
        return None

    hist = df.groupby(["agent_id", "topic"], as_index=False)["last_coached_period"].max()
    log.info("coaching_history: built %d agent x topic dampening rows.", len(hist))
    return hist
