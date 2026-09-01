"""Interpret raw TrAIning Assist behavior rows into skill-level pass-rates.

The raw extract (`training_assist.csv`, produced by extraction/sql/training_assist.sql.j2)
is tall at the behavior grain: one row per
`(agent_id, week_ending, call_type, scorecard_name=challenge_id, behavior)`.

This module reads the governed mapping `configs/mappings/training_profiles.yaml` and
reframes those rows from raw behaviors/profiles into the **skills** experts are training
on, applying the reference material's relevance rules:

  1. behavior -> skill   via `skills.<id>.behavior_names` (each behavior maps to one skill).
  2. relevance filter     a behavior counts toward a skill only where that profile
                          (challenge_id) marks the skill as a requirement level whose
                          `include_in_scoring` is true (always / conditional / as_needed).
                          `never` and unspecified skills are dropped
                          (`defaults.unspecified_requirement: never`).
  3. aggregate to skill   sum numerator/denominator/attempts per
                          (agent_id, week_ending, call_type, skill); recompute calc.

Note on `requires_opportunity` (conditional / as_needed): a raw row exists only when the
behavior was actually evaluated in a session, so the opportunity is already present -- no
extra filter is needed beyond "the row exists and the skill is a counted requirement".

Output is the tall-skinny contract build_signals consumes (`metric_key` column = `skill`,
value = `calc`), plus human-readable `skill_label` / `category` columns for inspection
(build_signals ignores unlisted columns).
"""
from __future__ import annotations

from typing import Any, Dict, List, Set

import pandas as pd

from cde.utils.logging import get_logger

log = get_logger(__name__)

# Output columns (week key mirrors behavior_scores: raw emits `week_ending`).
_OUT_COLS: List[str] = [
    "agent_id",
    "week_ending",
    "call_type",
    "skill",
    "skill_label",
    "category",
    "category_label",
    "attempts",
    "numerator",
    "denominator",
    "calc",
]


def _norm(x: Any) -> str:
    """Lowercase + strip for case-insensitive join keys (behaviors, challenge_ids)."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    return str(x).strip().lower()


def _counted_levels(profiles: Dict[str, Any]) -> Set[str]:
    """Requirement levels whose `include_in_scoring` is true (e.g. always/conditional/as_needed)."""
    rules = profiles.get("requirement_rules") or {}
    counted = {lvl for lvl, r in rules.items() if (r or {}).get("include_in_scoring")}
    if not counted:
        # Defensive fallback if the config omits requirement_rules.
        counted = {"always", "conditional", "as_needed"}
    return counted


def build_behavior_skill_map(profiles: Dict[str, Any]) -> Dict[str, str]:
    """behavior (normalized) -> skill_id, from skills.<id>.behavior_names."""
    b2s: Dict[str, str] = {}
    for sid, meta in (profiles.get("skills") or {}).items():
        for beh in (meta.get("behavior_names") or []):
            key = _norm(beh)
            if not key:
                continue
            if key in b2s and b2s[key] != sid:
                # No collisions today, but guard against a future edit silently reweighting.
                log.warning(
                    "training_assist: behavior %r maps to multiple skills (%s, %s); keeping %s",
                    key, b2s[key], sid, b2s[key],
                )
                continue
            b2s[key] = sid
    return b2s


def build_profile_requirements(
    profiles: Dict[str, Any], counted_levels: Set[str]
) -> Dict[str, Set[str]]:
    """challenge_id (normalized) -> set of skill_ids that count for that profile."""
    out: Dict[str, Set[str]] = {}
    for pid, prof in (profiles.get("profiles") or {}).items():
        cid = _norm(prof.get("challenge_id") or pid)
        req = prof.get("requirements") or {}
        relevant: Set[str] = set()
        for level, skill_list in req.items():
            if level not in counted_levels:  # e.g. an explicit `never` section
                continue
            for sid in (skill_list or []):
                relevant.add(sid)
        out[cid] = relevant
    return out


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_OUT_COLS)


def build_training_assist_skills(
    raw_df: pd.DataFrame, profiles: Dict[str, Any]
) -> pd.DataFrame:
    """Roll raw behavior rows up to relevance-filtered skill-level pass-rates.

    Parameters
    ----------
    raw_df : DataFrame with columns
        agent_id, week_ending, call_type, scorecard_name, behavior,
        attempts, numerator, denominator, calc
    profiles : parsed configs/mappings/training_profiles.yaml
    """
    if raw_df is None or raw_df.empty:
        return _empty_frame()

    b2s = build_behavior_skill_map(profiles)
    counted = _counted_levels(profiles)
    prof_req = build_profile_requirements(profiles, counted)
    skills_meta = profiles.get("skills") or {}
    cats = profiles.get("skill_categories") or {}

    week_col = "week_ending" if "week_ending" in raw_df.columns else "period"

    df = raw_df.copy()
    df["_behavior"] = df["behavior"].map(_norm)
    df["_challenge"] = df["scorecard_name"].map(_norm)
    df["skill"] = df["_behavior"].map(b2s)

    # --- data-quality visibility (first-pass mapping has known gaps) ---
    unmapped_beh = sorted(set(df.loc[df["skill"].isna(), "_behavior"]) - {""})
    if unmapped_beh:
        log.info(
            "training_assist: %d behavior(s) have no skill mapping (rows dropped): %s",
            len(unmapped_beh), unmapped_beh[:25],
        )
    unknown_ch = sorted(set(df["_challenge"]) - set(prof_req) - {""})
    if unknown_ch:
        log.warning(
            "training_assist: %d challenge_id(s) not found in training_profiles.yaml "
            "(rows dropped -- reconcile scorecard_name vs profiles.<id>.challenge_id): %s",
            len(unknown_ch), unknown_ch[:25],
        )

    df = df[df["skill"].notna()].copy()

    # --- relevance filter: skill must be a counted requirement for THIS profile ---
    relevant_mask = [
        skill in prof_req.get(challenge, ())
        for skill, challenge in zip(df["skill"], df["_challenge"])
    ]
    dropped_irrelevant = int(len(df) - sum(relevant_mask))
    if dropped_irrelevant:
        log.info(
            "training_assist: dropped %d behavior row(s) not required by their profile "
            "(never / unspecified).", dropped_irrelevant,
        )
    df = df[pd.Series(relevant_mask, index=df.index)].copy()

    if df.empty:
        return _empty_frame()

    # --- aggregate to skill grain ---
    df["numerator"] = pd.to_numeric(df["numerator"], errors="coerce")
    df["denominator"] = pd.to_numeric(df["denominator"], errors="coerce")
    df["attempts"] = pd.to_numeric(df.get("attempts"), errors="coerce")

    grp = (
        df.groupby(["agent_id", week_col, "call_type", "skill"], dropna=False)
        .agg(
            attempts=("attempts", "sum"),
            numerator=("numerator", "sum"),
            denominator=("denominator", "sum"),
        )
        .reset_index()
    )

    denom = grp["denominator"].where(grp["denominator"] != 0)
    grp["calc"] = grp["numerator"] / denom

    grp["skill_label"] = grp["skill"].map(lambda s: (skills_meta.get(s) or {}).get("label"))
    grp["category"] = grp["skill"].map(lambda s: (skills_meta.get(s) or {}).get("category"))
    grp["category_label"] = grp["category"].map(lambda c: (cats.get(c) or {}).get("label"))

    if week_col != "week_ending":
        grp = grp.rename(columns={week_col: "week_ending"})

    return grp[_OUT_COLS].sort_values(
        ["agent_id", "week_ending", "skill"]
    ).reset_index(drop=True)
