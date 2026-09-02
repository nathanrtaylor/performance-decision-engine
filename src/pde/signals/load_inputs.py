# src/pde/signals/load_inputs.py
from __future__ import annotations

from typing import Dict, Optional

import pandas as pd

from pde.io.readers import load_raw_weekly


def load_normalized_for_signals(run_id: Optional[str] = None) -> Dict[str, pd.DataFrame]:
    """
    Loads raw weekly extracts and returns the 'normalized' dict expected by build_signals():
      keys must match source_catalog.sources names (e.g., agent_metrics, behavior_scores, agents).
    """
    raw = load_raw_weekly(run_id)

    normalized: Dict[str, pd.DataFrame] = {}

    if raw.agent_metrics is not None:
        normalized["agent_metrics"] = raw.agent_metrics

    if raw.behavior_scores is not None:
        normalized["behavior_scores"] = raw.behavior_scores

    if raw.agents is not None:
        normalized["agents"] = raw.agents

    return normalized