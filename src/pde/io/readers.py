from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from .paths import get_raw_weekly_path


@dataclass(frozen=True)
class RawWeeklyData:
    agents: Optional[pd.DataFrame]
    agent_metrics: Optional[pd.DataFrame]
    behavior_scores: Optional[pd.DataFrame]


def _read_csv_if_exists(path) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    return pd.read_csv(path)


def load_raw_weekly(run_id: Optional[str] = None) -> RawWeeklyData:
    """
    Loads canonical raw weekly datasets. Missing datasets return None.
    """
    base = get_raw_weekly_path(run_id)

    agents = _read_csv_if_exists(base / "agents.csv")
    agent_metrics = _read_csv_if_exists(base / "agent_metrics.csv")
    behavior_scores = _read_csv_if_exists(base / "behavior_scores.csv")

    return RawWeeklyData(
        agents=agents,
        agent_metrics=agent_metrics,
        behavior_scores=behavior_scores,
    )