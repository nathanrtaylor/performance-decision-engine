# src/pde/signals/derived_metrics.py
"""
Derived (composite) metrics: build a metric from OTHER metrics' numerator/denominator columns.

Some coaching metrics are not stored as their own row in the source table, but can be constructed
from rows that are. A derived metric declares, in metric_catalog.yaml:

    sp100:
      source: derived
      source_metric_key: sp100          # self-referential: the value stamped on synthesized rows
      derived:
        numerator:
          - { metric_key: "enrolled" }                       # part defaults to numerator
        denominator: { metric_key: "sales opportunities", part: numerator }

    talk_plus_acw_per_call:
      source: derived
      source_metric_key: talk_plus_acw_per_call
      derived:
        numerator:
          - { metric_key: "talk time" }
          - { metric_key: "acw" }
        denominator: { metric_key: "talk time", part: denominator }   # the shared 'calls' column

Value semantics (locked with SME):
  numerator   = SUM over the numerator refs of each ref's chosen column (default: numerator)
  denominator =            the single denominator ref's chosen column   (default: denominator)
  calc        = numerator / denominator

``metric_key`` references are RAW source_metric_keys (source-level building blocks); they need not be
their own catalog metrics. A group (agent x period x call_type x icp_client x site) that is missing
ANY referenced component, or whose denominator is 0/NaN, is SKIPPED (never fabricated from partials).

This module is a pure transform used at two call sites, both operating on a tall agent-metrics frame
whose per-row metric label lives in ``metric_key_col``:
  * the main pipeline (src/pde/cli/run_pipeline.py), between normalize_inputs and build_signals
  * benchmark recalculation (src/pde/benchmarks_recalc/prep.py)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

import pandas as pd

from pde.utils.logging import get_logger

log = get_logger(__name__)

# Grouping grain: whichever of these identify a single source metric row are used as the join key.
_KEY_CANDIDATES = ["agent_id", "period", "week_ending", "week_start", "call_type", "icp_client", "site"]
_OUT_VALUE_COLS = ["metric", "numerator", "denominator", "calc"]
_VALID_PARTS = ("numerator", "denominator")


def _catalog_metrics(config: Dict[str, Any]) -> Dict[str, Any]:
    """Unwrap cfg['metric_catalog'] -> {metric: entry} (resolve_active_config keeps the wrapper)."""
    mc = config.get("metric_catalog") or {}
    if isinstance(mc, dict) and "metric_catalog" in mc:
        mc = mc["metric_catalog"]
    return (mc or {}).get("metrics", {}) or {}


def _norm_refs(raw: Any, default_part: str) -> List[Dict[str, str]]:
    """Normalize a numerator/denominator spec (list or single dict) to [{metric_key, part}]."""
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    refs: List[Dict[str, str]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        mk = it.get("metric_key")
        if not mk:
            continue
        part = str(it.get("part", default_part)).strip().lower()
        if part not in _VALID_PARTS:
            part = default_part
        refs.append({"metric_key": str(mk).strip(), "part": part})
    return refs


def derived_defs(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Return {canonical_metric: {"numerator": [ref,...], "denominator": ref}} for every catalog metric
    carrying a well-formed ``derived`` block. Malformed blocks (missing numerator/denominator) are
    skipped here and surfaced by config_lint.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for name, entry in _catalog_metrics(config).items():
        d = (entry or {}).get("derived")
        if not isinstance(d, dict):
            continue
        num_refs = _norm_refs(d.get("numerator"), default_part="numerator")
        den_refs = _norm_refs(d.get("denominator"), default_part="denominator")
        if not num_refs or not den_refs:
            log.warning("derived_metrics: metric %r has a malformed 'derived' block; skipping.", name)
            continue
        out[str(name)] = {"numerator": num_refs, "denominator": den_refs[0]}
    return out


def derived_component_keys(config: Dict[str, Any]) -> Set[str]:
    """All raw source_metric_keys referenced by any derived metric (for extraction pull + lint)."""
    keys: Set[str] = set()
    for spec in derived_defs(config).values():
        keys.update(r["metric_key"] for r in spec["numerator"])
        keys.add(spec["denominator"]["metric_key"])
    return keys


def _empty(key_cols: List[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=key_cols + _OUT_VALUE_COLS)


def _ref_frame(
    work: pd.DataFrame, key_cols: List[str], norm_col: str, ref: Dict[str, str], value_name: str
) -> Optional[pd.DataFrame]:
    """Rows for one component ref, reduced to key_cols + a single value column (the chosen part).

    Matching on the component key is case-insensitive (source metric strings vary in case; the
    extract filters with LOWER(...) but keeps original case in the CSV), so a casing mismatch can
    never silently yield zero composite rows.
    """
    sub = work[work[norm_col] == ref["metric_key"].strip().lower()]
    if sub.empty:
        return None
    f = sub[key_cols].copy()
    f[value_name] = pd.to_numeric(sub[ref["part"]], errors="coerce").to_numpy()
    f = f.dropna(subset=[value_name])
    if f.empty:
        return None
    # One row per grain key is expected; guard against accidental dupes so the merge stays 1:1.
    return f.drop_duplicates(subset=key_cols)


def synthesize_derived(
    df: Optional[pd.DataFrame], config: Dict[str, Any], *, metric_key_col: str = "metric"
) -> pd.DataFrame:
    """
    Build composite metric rows from a tall agent-metrics frame.

    ``df`` must carry ``metric_key_col`` plus ``numerator``/``denominator`` columns and one or more of
    the ``_KEY_CANDIDATES``. Returns a frame with the same key columns plus metric/numerator/
    denominator/calc, one row per (grain key) per derived metric that had all its components present.
    Empty (well-formed) when nothing is configured or nothing qualifies.
    """
    key_cols = [c for c in _KEY_CANDIDATES if df is not None and c in df.columns]
    defs = derived_defs(config)
    if not defs or df is None or df.empty or metric_key_col not in df.columns or not key_cols:
        return _empty(key_cols)

    work = df.copy()
    work["_mk"] = work[metric_key_col].astype(str).str.strip().str.lower()
    for col in ("numerator", "denominator"):
        if col not in work.columns:
            work[col] = pd.NA

    results: List[pd.DataFrame] = []
    for name, spec in defs.items():
        merged: Optional[pd.DataFrame] = None
        num_value_cols: List[str] = []
        missing = False
        for i, ref in enumerate(spec["numerator"]):
            vc = f"__num{i}"
            f = _ref_frame(work, key_cols, "_mk", ref, vc)
            if f is None:
                missing = True
                break
            num_value_cols.append(vc)
            merged = f if merged is None else merged.merge(f, on=key_cols, how="inner")
        if missing or merged is None or merged.empty:
            continue

        fd = _ref_frame(work, key_cols, "_mk", spec["denominator"], "__den")
        if fd is None:
            continue
        merged = merged.merge(fd, on=key_cols, how="inner")  # inner join skips groups missing a part
        if merged.empty:
            continue

        num = merged[num_value_cols].sum(axis=1)
        den = merged["__den"]
        keep = num.notna() & den.notna() & (den != 0)
        if not keep.any():
            continue

        res = merged.loc[keep, key_cols].copy()
        res["metric"] = name
        res["numerator"] = num[keep].to_numpy()
        res["denominator"] = den[keep].to_numpy()
        res["calc"] = (num[keep] / den[keep]).to_numpy()
        results.append(res)

    if not results:
        return _empty(key_cols)
    return pd.concat(results, ignore_index=True)[key_cols + _OUT_VALUE_COLS]
