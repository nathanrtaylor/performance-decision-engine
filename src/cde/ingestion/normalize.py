from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from cde.utils.config import unwrap_root
from cde.utils.logging import get_logger

log = get_logger(__name__)


def _cohort_splits(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Composite-cohort split rules from cfg['cohort_map'] (mappings auto-load). [] when absent."""
    cm = unwrap_root(config.get("cohort_map") or {}, "cohort_map")
    return list(cm.get("splits") or []) if isinstance(cm, dict) else []


def derive_cohort(df: pd.DataFrame, splits: List[Dict[str, Any]]) -> pd.DataFrame:
    """Overwrite ``icp_client`` with a composite cohort label per configs/mappings/cohort_map.yaml.

    For each rule ``{icp_client, by: client|subclient, values?}``, rows whose ``icp_client`` equals
    the rule's base AND whose ``by`` field is a non-blank value (restricted to ``values`` when given)
    are relabeled ``f"{icp_client}::{value}"``. Every other row keeps its ``icp_client`` unchanged, so
    this is a no-op when there are no rules or the frame lacks the needed columns. Emits already
    strip/lower-normalized labels (matching build_signals' cohort normalization), so the rest of the
    engine keys on the derived string with zero changes.
    """
    if not splits or df is None or df.empty or "icp_client" not in df.columns:
        return df
    icp = df["icp_client"].astype("string").str.strip().str.lower()
    label = icp.copy()
    for rule in splits:
        base = str(rule.get("icp_client", "")).strip().lower()
        field = rule.get("by")
        if not base or field not in ("client", "subclient") or field not in df.columns:
            continue
        fld = df[field].astype("string").str.strip().str.lower()
        match = (icp == base) & fld.notna() & (fld != "")
        vals = rule.get("values")
        if vals:
            allow = {str(v).strip().lower() for v in vals}
            match = match & fld.isin(allow)
        # relabel matched rows -> "<icp>::<field value>"; leave the rest as-is
        label = label.mask(match, icp.str.cat(fld, sep="::"))
    df = df.copy()
    df["icp_client"] = label.replace({"": pd.NA})
    return df


def normalize_inputs(raw, config: Dict[str, Any]) -> Dict[str, pd.DataFrame]:
    """
    Normalizes raw tables into canonical column names and a minimal engine input mart.

    Strategy:
    - apply table-specific renames from config (optional)
    - apply minimal canonicalization for known core tables (e.g., agent_metrics)
    - optionally build a merged 'engine_inputs' table if config declares join keys and sources

    This keeps PoC friction low while enforcing canonical naming and data types where it matters.
    """
    renames_cfg = (config.get("normalization") or {}).get("renames", {})
    out: Dict[str, pd.DataFrame] = {}

    for name, df in raw.tables.items():
        df2 = df.copy()

        # 1) Config-driven renames first
        if name in renames_cfg:
            df2 = df2.rename(columns=renames_cfg[name])

        # 2) Minimal canonicalization for core tables
        if name == "agent_metrics":
            # Ensure canonical period exists (support raw week_ending)
            if "period" not in df2.columns and "week_ending" in df2.columns:
                df2 = df2.rename(columns={"week_ending": "period"})

            # Coerce period to datetime early (prevents downstream merge dtype mismatches)
            if "period" in df2.columns:
                df2["period"] = pd.to_datetime(df2["period"], errors="coerce")

            # Coerce numeric inputs
            if "numerator" in df2.columns:
                df2["numerator"] = pd.to_numeric(df2["numerator"], errors="coerce")
            if "denominator" in df2.columns:
                df2["denominator"] = pd.to_numeric(df2["denominator"], errors="coerce")

            # Ensure value exists and compute deterministically when possible
            if "value" not in df2.columns:
                df2["value"] = np.nan

            if "numerator" in df2.columns and "denominator" in df2.columns:
                mask = (
                    df2["value"].isna()
                    & df2["numerator"].notna()
                    & df2["denominator"].notna()
                    & (df2["denominator"] != 0)
                )
                df2.loc[mask, "value"] = df2.loc[mask, "numerator"] / df2.loc[mask, "denominator"]

            # Standardize metric strings (helps benchmark/topic joins)
            if "metric" in df2.columns:
                df2["metric"] = df2["metric"].astype(str).str.strip()

        out[name] = df2

    # Composite cohorts: overwrite icp_client with the derived label (icp_client::value) per
    # cohort_map split rules, on every frame that carries a cohort. No-op when no rules are set.
    splits = _cohort_splits(config)
    if splits:
        for name in out:
            out[name] = derive_cohort(out[name], splits)

    # Optional: build engine_inputs by joining declared sources on entity keys
    join_cfg = (config.get("normalization") or {}).get("build_engine_inputs")
    if join_cfg:
        # Default entity keys should be canonical. If not specified, assume "period".
        entity = config.get("entity_keys", {"agent_id": "agent_id", "period": "period"})
        agent_key = entity["agent_id"]
        period_key = entity["period"]

        sources = join_cfg.get("sources", [])
        if not sources:
            raise ValueError("normalization.build_engine_inputs.sources is empty.")

        base_name = sources[0]
        if base_name not in out:
            raise ValueError(f"Base source table missing: {base_name}")

        merged = out[base_name]
        for other in sources[1:]:
            if other not in out:
                raise ValueError(f"Join source table missing: {other}")
            merged = merged.merge(
                out[other],
                on=[agent_key, period_key],
                how="left",
                suffixes=("", f"__{other}"),
            )

        out["engine_inputs"] = merged
        log.info("Built 'engine_inputs' by joining sources: %s", sources)

    return out