from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from pde.utils.config import unwrap_root as _unwrap
from pde.utils.ids import normalize_agent_id
from pde.utils.logging import get_logger

log = get_logger(__name__)


def _filter_coaching_types(df: pd.DataFrame, count_types: set) -> pd.DataFrame:
    """
    Restrict coaching events to an allow-list of ``coaching_type`` values (case-insensitive).

    Only these coaching types count as "coached" for dampening — e.g. restrict to ADAPT so
    that only structured ADAPT coaching suppresses re-coaching, while informal types (huddles,
    "In The Game", etc.) do not. The allow-list is expandable via
    coaching_history_map.count_types.

    An empty allow-list means ALL types count (backward compatible). If the allow-list is set
    but the frame has no ``coaching_type`` column, the filter is skipped with a warning (it
    cannot be enforced), mirroring the defensive count_status handling.
    """
    if not count_types:
        return df
    if "coaching_type" not in df.columns:
        log.warning(
            "coaching_history: count_types %s set but no 'coaching_type' column; type filter skipped.",
            sorted(count_types),
        )
        return df
    allow = {str(t).strip().casefold() for t in count_types}
    return df[df["coaching_type"].astype(str).str.strip().str.casefold().isin(allow)]


def map_coaching_events(
    raw: Optional[pd.DataFrame], config: Dict[str, Any]
) -> Optional[pd.DataFrame]:
    """
    Behavior-level coaching events mapped to engine topics — the shared basis for both
    dampening (build_coaching_history) and the dampening-evidence export.

    Applies the governed crosswalk (configs/mappings/coaching_history_map.yaml): the
    count_status and count_types allow-lists, then behavior_selected -> topic (case-insensitive;
    unmapped behaviors are logged and dropped). Returns ONE ROW PER coaching event (not collapsed)
    with columns:

        agent_id (normalized) | topic | coach_period | coaching_date | behavior | coaching_status

    ``coach_period`` is the dampening date driver (the weekly ``period`` bucket when present, else
    ``coaching_date``); ``coaching_date`` is the actual event day retained for human-readable
    evidence. Returns None when there is nothing to map (no table, empty after filters, missing
    map_key/date columns).
    """
    if raw is None or getattr(raw, "empty", True):
        return None

    xmap = _unwrap(config.get("coaching_history_map") or {}, "coaching_history_map")
    map_key = xmap.get("map_key", "behavior_selected")
    count_status = set(xmap.get("count_status") or [])
    count_types = set(xmap.get("count_types") or [])
    behavior_to_topic = xmap.get("behavior_to_topic") or {}

    df = raw.copy()

    # 1) keep only counted coaching statuses (defensive; also filtered at extraction)
    if count_status and "coaching_status" in df.columns:
        df = df[df["coaching_status"].astype(str).str.strip().isin(count_status)]
    if df.empty:
        return None

    # 1b) keep only counted coaching types (allow-list; empty => all types count)
    df = _filter_coaching_types(df, count_types)
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
    df["behavior"] = subject
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

    # 3) canonical agent_id + the date driver (weekly `period` bucket preferred, else coaching_date)
    df["agent_id"] = normalize_agent_id(df["agent_id"])
    driver_col = "period" if "period" in df.columns else ("coaching_date" if "coaching_date" in df.columns else None)
    if driver_col is None:
        log.warning("coaching_history: no 'period'/'coaching_date' column; no dampening applied.")
        return None

    df["coach_period"] = pd.to_datetime(df[driver_col], errors="coerce")
    df["coaching_date"] = pd.to_datetime(
        df["coaching_date"] if "coaching_date" in df.columns else df[driver_col], errors="coerce"
    )
    df["coaching_status"] = df["coaching_status"].astype(str) if "coaching_status" in df.columns else pd.NA
    df = df[df["agent_id"].notna() & df["coach_period"].notna()]
    if df.empty:
        return None

    return df[["agent_id", "topic", "coach_period", "coaching_date", "behavior", "coaching_status"]].reset_index(drop=True)


def build_coaching_history(
    normalized: Dict[str, pd.DataFrame], config: Dict[str, Any]
) -> Optional[pd.DataFrame]:
    """
    Collapse raw coaching events into the dampening input grain:

        agent_id | topic | last_coached_period

    Uses the governed crosswalk (configs/mappings/coaching_history_map.yaml) via
    ``map_coaching_events`` to map each event's ``behavior_selected`` to an engine topic, then keeps
    the most recent coached period per (agent, topic). Returns None when no ``coaching_history``
    table is present (or nothing maps), so the pipeline degrades gracefully to no dampening.
    """
    events = map_coaching_events(normalized.get("coaching_history"), config)
    if events is None or events.empty:
        return None

    hist = (
        events.groupby(["agent_id", "topic"], as_index=False)["coach_period"]
        .max()
        .rename(columns={"coach_period": "last_coached_period"})
    )
    log.info("coaching_history: built %d agent x topic dampening rows.", len(hist))
    return hist
