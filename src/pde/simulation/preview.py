from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Tuple

import pandas as pd

from pde.engine.recommend import recommend_for_population
from pde.simulation.impact import topic_distribution


def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def preview_scenarios(candidates: pd.DataFrame, base_config: Dict[str, Any], scenarios: List[Dict[str, Any]]) -> Tuple[pd.DataFrame, str]:
    """
    Returns:
      - summary dataframe (scenario -> distribution deltas)
      - json details (full distributions per scenario)
    """
    base_recs = recommend_for_population(candidates, base_config)
    base_dist = topic_distribution(base_recs)

    details = {"base": base_dist.to_dict(orient="records"), "scenarios": []}
    summary_rows = []

    for s in scenarios:
        name = s.get("name", "unnamed")
        changes = s.get("changes") or {}
        cfg = _deep_merge(base_config, changes)
        cfg_meta = cfg.get("meta") or {}
        cfg_meta["version"] = f"{cfg_meta.get('version', 'base')}__scenario__{name}"
        cfg["meta"] = cfg_meta

        recs = recommend_for_population(candidates, cfg)
        dist = topic_distribution(recs)

        # summarize: count topic changes vs base
        merged = base_recs.merge(
            recs,
            on=["agent_id", "period", "call_type"],
            suffixes=("_base", "_scenario"),
            how="inner",
        )
        changed = (merged["topic_base"] != merged["topic_scenario"]).sum()

        summary_rows.append(
            {
                "scenario": name,
                "n_recommendations": len(recs),
                "n_topic_changed_vs_base": int(changed),
            }
        )

        details["scenarios"].append(
            {"name": name, "distribution": dist.to_dict(orient="records")}
        )

    summary_df = pd.DataFrame(summary_rows)
    return summary_df, json.dumps(details, ensure_ascii=False, indent=2)
