"""Training class roster — the source of truth for the dashboard population.

Loads the Training Orchestra export (docs/training/training_class_roster.xlsx,
sheet "Expert Roster") into a normalized frame. This defines WHO shows up in the
training dashboard, their class/session id, trainer, and training start date (the
day the training-timeline count begins).

Roster columns -> canonical:
    Expert Name  -> agent_name
    EEID         -> agent_id          (employee id; joins to the skill data's agent_id)
    Trainer Name -> trainer
    Start Date   -> training_start_date
    Class Type   -> class_type         (New Hire | Tenure Conversion | ...)
    Session #    -> class_id           (the class / session id, e.g. S-04191)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from cde.utils.logging import get_logger

log = get_logger(__name__)

_SHEET = "Expert Roster"
_RENAME = {
    "Expert Name": "agent_name",
    "EEID": "agent_id",
    "Trainer Name": "trainer",
    "Start Date": "training_start_date",
    "Class Type": "class_type",
    "Session #": "class_id",
}
_COLS = ["agent_id", "agent_name", "class_id", "trainer", "training_start_date", "class_type", "icp_client"]


def load_class_roster(path: str | Path, *, icp_client: str = "training") -> pd.DataFrame:
    """Return the roster as a normalized agents frame (one row per expert)."""
    df = pd.read_excel(Path(path), sheet_name=_SHEET)
    df = df.rename(columns=_RENAME)

    missing = [c for c in ("agent_id", "class_id", "trainer", "training_start_date") if c not in df.columns]
    if missing:
        raise ValueError(f"roster {path!r} sheet {_SHEET!r} missing columns {missing}; got {list(df.columns)}")

    df = df[df["agent_id"].notna()].copy()
    df["agent_id"] = (df["agent_id"].astype("Int64").astype(str)   # 725820.0 -> "725820"
                      .str.strip())
    df["training_start_date"] = pd.to_datetime(df["training_start_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for c in ("agent_name", "trainer", "class_id", "class_type"):
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()
    df["icp_client"] = icp_client

    out = df[[c for c in _COLS if c in df.columns]].drop_duplicates("agent_id").reset_index(drop=True)
    log.info("roster: %d experts across %d classes from %s",
             len(out), out["class_id"].nunique(), Path(path).name)
    return out
