"""
Load the latest extract and reduce it to the windowed-mean-per-agent grain the engine scores on.

Deliberately resolves the raw dir the SAME way the pipeline does (governance.resolve_raw_export_dir),
NOT via io.readers.load_raw_weekly / io.paths.get_raw_weekly_path -- those point at
``configs/data/raw/weekly/...`` which is a different (empty) root; the real extract lives at
``data/raw/weekly/...``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from cde.governance.versioning import resolve_raw_export_dir
from cde.utils.logging import get_logger

from .config import WINDOW_WEEKS

log = get_logger(__name__)


@dataclass(frozen=True)
class MetricMeta:
    metric: str
    source: str
    source_metric_key: str
    category: str
    direction: str
    unit: str
    benchmark_key: str
    benchmark_type: str
    denominator_min: Optional[float]


@dataclass(frozen=True)
class RawFrames:
    agents: Optional[pd.DataFrame]
    agent_metrics: Optional[pd.DataFrame]
    behavior_scores: Optional[pd.DataFrame]
    raw_dir: Path
    snapshot_id: str


@dataclass(frozen=True)
class PreppedFrames:
    agent_metrics: pd.DataFrame          # + 'metric_key', normalized lowercase 'icp_client'
    behavior_scores: pd.DataFrame        # + 'metric_key','scorecard_name', joined 'icp_client'
    metric_meta: Dict[str, MetricMeta]   # canonical metric -> contract
    behavior_scorecards: Dict[str, str]  # canonical behavior metric -> dominant scorecard_name
    window_weeks: list = field(default_factory=list)
    snapshot_id: str = ""
    unmapped_counts: Dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------------------------------
# config helpers
# ---------------------------------------------------------------------------------------------------

def _catalog_metrics(config: Dict[str, Any]) -> Dict[str, Any]:
    """Unwrap cfg['metric_catalog'] -> {metric: entry}. resolve_active_config keeps the wrapper."""
    mc = config.get("metric_catalog") or {}
    if isinstance(mc, dict) and "metric_catalog" in mc:
        mc = mc["metric_catalog"]
    return (mc or {}).get("metrics", {}) or {}


def build_metric_meta(config: Dict[str, Any]) -> Dict[str, MetricMeta]:
    """Canonical metric -> MetricMeta pulled from metric_catalog."""
    out: Dict[str, MetricMeta] = {}
    for metric, entry in _catalog_metrics(config).items():
        entry = entry or {}
        over = entry.get("computation_override") or {}
        bench = entry.get("benchmark") or {}
        dmin = over.get("denominator_min")
        out[metric] = MetricMeta(
            metric=metric,
            source=str(entry.get("source", "")),
            source_metric_key=str(entry.get("source_metric_key", "")),
            category=str(entry.get("category", "")),
            direction=str(entry.get("direction", "higher_is_better")),
            unit=str(entry.get("unit", "")),
            benchmark_key=str((bench.get("key") or metric)),
            benchmark_type=str(bench.get("type", "config")),
            denominator_min=(float(dmin) if dmin is not None else None),
        )
    return out


def build_source_key_map(meta: Dict[str, MetricMeta]) -> Dict[Tuple[str, str], str]:
    """(source, source_metric_key) -> canonical metric. Mirrors build_signals' mapping."""
    return {(m.source, m.source_metric_key.strip()): m.metric for m in meta.values()}


# ---------------------------------------------------------------------------------------------------
# load + prep
# ---------------------------------------------------------------------------------------------------

def _resolve_snapshot_id(raw_dir: Path) -> str:
    manifest = raw_dir / "manifest.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if data.get("run_id"):
                return str(data["run_id"])
        except Exception:  # noqa: BLE001
            pass
    return raw_dir.name


def _read_csv(path: Path) -> Optional[pd.DataFrame]:
    return pd.read_csv(path) if path.exists() else None


def load_latest_extract(
    configs_dir: Path, config: Dict[str, Any], raw_dir: Optional[Path] = None
) -> RawFrames:
    rd = Path(raw_dir) if raw_dir else resolve_raw_export_dir(configs_dir, config)
    return RawFrames(
        agents=_read_csv(rd / "agents.csv"),
        agent_metrics=_read_csv(rd / "agent_metrics.csv"),
        behavior_scores=_read_csv(rd / "behavior_scores.csv"),
        raw_dir=rd,
        snapshot_id=_resolve_snapshot_id(rd),
    )


def _last_n_weeks(df: pd.DataFrame, n: int = WINDOW_WEEKS) -> Tuple[pd.DataFrame, list]:
    """Restrict to the last ``n`` distinct week_ending values (the scoring window)."""
    if "week_ending" not in df.columns or df.empty:
        return df, []
    weeks = pd.to_datetime(df["week_ending"], errors="coerce")
    distinct = sorted(w for w in weeks.dropna().unique())
    keep = set(distinct[-n:])
    out = df[weeks.isin(keep)].copy()
    return out, [pd.Timestamp(w) for w in sorted(keep)]


def prep_frames(raw: RawFrames, config: Dict[str, Any]) -> PreppedFrames:
    meta = build_metric_meta(config)
    key_map = build_source_key_map(meta)
    unmapped: Dict[str, int] = {}

    # -- agent_metrics: map canonical key, normalize cohort, window --
    am = raw.agent_metrics.copy() if raw.agent_metrics is not None else pd.DataFrame()
    if not am.empty:
        am["agent_id"] = am["agent_id"].astype(str)
        am["icp_client"] = am["icp_client"].astype(str).str.strip().str.lower()
        am["metric_key"] = am["metric"].astype(str).str.strip().map(
            lambda k: key_map.get(("agent_metrics", k))
        )
        unmapped["agent_metrics"] = int(am["metric_key"].isna().sum())
        am = am[am["metric_key"].notna()].copy()
        am, am_weeks = _last_n_weeks(am)
    else:
        am_weeks = []

    # -- behavior_scores: map canonical key, join cohort from agents, window --
    bs = raw.behavior_scores.copy() if raw.behavior_scores is not None else pd.DataFrame()
    behavior_scorecards: Dict[str, str] = {}
    if not bs.empty:
        bs["agent_id"] = bs["agent_id"].astype(str)
        bs["metric_key"] = bs["behavior"].astype(str).str.strip().map(
            lambda k: key_map.get(("behavior_scores", k))
        )
        unmapped["behavior_scores"] = int(bs["metric_key"].isna().sum())
        bs = bs[bs["metric_key"].notna()].copy()

        # cohort join from agents on (agent_id, week_ending) -- behavior_scores has no icp_client
        if raw.agents is not None and not raw.agents.empty:
            ag = raw.agents[["agent_id", "week_ending", "icp_client"]].copy()
            ag["agent_id"] = ag["agent_id"].astype(str)
            ag["icp_client"] = ag["icp_client"].astype(str).str.strip().str.lower()
            bs = bs.merge(ag, on=["agent_id", "week_ending"], how="left")
        else:
            bs["icp_client"] = pd.NA

        bs, _ = _last_n_weeks(bs)

        # dominant scorecard per canonical behavior metric (sentiment vs quality routing)
        if "scorecard_name" in bs.columns:
            for metric_key, grp in bs.groupby("metric_key"):
                mode = grp["scorecard_name"].astype(str).mode()
                behavior_scorecards[str(metric_key)] = str(mode.iloc[0]) if not mode.empty else ""

    for src, n in unmapped.items():
        if n:
            log.info("benchmarks_recalc: dropped %d unmapped %s rows (no metric_catalog match).", n, src)

    return PreppedFrames(
        agent_metrics=am,
        behavior_scores=bs,
        metric_meta=meta,
        behavior_scorecards=behavior_scorecards,
        window_weeks=am_weeks,
        snapshot_id=raw.snapshot_id,
        unmapped_counts=unmapped,
    )


def windowed_mean_per_agent(
    df: pd.DataFrame,
    metric_key: str,
    *,
    cohort_col: Optional[str] = "icp_client",
    denominator_min: Optional[float] = None,
) -> pd.DataFrame:
    """
    Collapse per-agent-per-week ``calc`` to one MEAN per agent over the window -- the grain the
    engine scores on. Optionally drops thin weekly rows (denominator < denominator_min) first,
    matching how temporal.aggregate treats low-evidence weeks.

    Returns columns: agent_id, [icp_client], mean_calc, n_weeks.
    """
    if df is None or df.empty:
        cols = ["agent_id"] + ([cohort_col] if cohort_col else []) + ["mean_calc", "n_weeks"]
        return pd.DataFrame(columns=cols)

    d = df[df["metric_key"] == metric_key].copy()
    d["calc"] = pd.to_numeric(d["calc"], errors="coerce")
    d = d.dropna(subset=["calc"])
    if denominator_min is not None and "denominator" in d.columns:
        den = pd.to_numeric(d["denominator"], errors="coerce")
        d = d[den >= float(denominator_min)]
    if d.empty:
        cols = ["agent_id"] + ([cohort_col] if cohort_col else []) + ["mean_calc", "n_weeks"]
        return pd.DataFrame(columns=cols)

    group_cols = ["agent_id"] + ([cohort_col] if cohort_col and cohort_col in d.columns else [])
    out = (
        d.groupby(group_cols, dropna=False)
        .agg(mean_calc=("calc", "mean"), n_weeks=("calc", "size"))
        .reset_index()
    )
    return out
