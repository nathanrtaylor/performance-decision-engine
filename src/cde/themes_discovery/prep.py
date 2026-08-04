"""
Prep for theme discovery: build a per-cohort agent x metric matrix on the direction-adjusted
"bad" axis (higher = worse), over the 8-week window.

Reuses the benchmarks_recalc extract loaders (same raw dir, same windowed-mean-per-agent grain)
so discovery scores on exactly the grain the engine scores on. The only new step is turning each
metric's per-agent windowed mean into a common "bad" axis and pivoting to agent x metric.
"""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

# Reuse the proven extract + windowing helpers from benchmarks_recalc.
from cde.benchmarks_recalc.prep import (  # noqa: F401  (re-exported for the CLI/tests)
    MetricMeta,
    PreppedFrames,
    RawFrames,
    build_metric_meta,
    load_latest_extract,
    prep_frames,
    windowed_mean_per_agent,
)
from cde.utils.logging import get_logger

log = get_logger(__name__)


def _source_frame(prepped: PreppedFrames, meta: MetricMeta) -> pd.DataFrame:
    if meta.source == "behavior_scores":
        return prepped.behavior_scores
    return prepped.agent_metrics


def build_bad_axis_by_cohort(prepped: PreppedFrames, config: Dict[str, Any]) -> Dict[str, pd.DataFrame]:
    """
    Return {cohort: DataFrame indexed by agent_id, one column per metric} of direction-adjusted
    windowed means (bad = mean if lower_is_better else -mean). Only metrics with data appear.
    """
    long_rows = []
    for metric, meta in prepped.metric_meta.items():
        frame = _source_frame(prepped, meta)
        if frame is None or frame.empty:
            continue
        wm = windowed_mean_per_agent(
            frame, metric, cohort_col="icp_client", denominator_min=meta.denominator_min
        )
        if wm.empty:
            continue
        lower_is_better = str(meta.direction) == "lower_is_better"
        wm = wm.copy()
        wm["bad"] = wm["mean_calc"] if lower_is_better else -wm["mean_calc"]
        wm["metric"] = metric
        cohort_col = "icp_client" if "icp_client" in wm.columns else None
        keep = ["agent_id", "metric", "bad"] + ([cohort_col] if cohort_col else [])
        long_rows.append(wm[keep])

    if not long_rows:
        return {}

    long = pd.concat(long_rows, ignore_index=True)
    if "icp_client" not in long.columns:
        long["icp_client"] = "all"
    long["icp_client"] = long["icp_client"].fillna("(unknown)").astype(str)

    out: Dict[str, pd.DataFrame] = {}
    for cohort, g in long.groupby("icp_client", sort=True):
        mat = g.pivot_table(index="agent_id", columns="metric", values="bad", aggfunc="mean")
        # need at least 2 metrics to correlate anything
        if mat.shape[1] >= 2:
            out[str(cohort)] = mat
    return out
