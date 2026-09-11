"""behavior->skill mapping + per-profile relevance filtering + skill-grain aggregation."""
import pandas as pd

from pde.ingestion.training_assist_skills import (
    ascend_challenge_ids,
    build_behavior_skill_map,
    build_profile_requirements,
    build_training_assist_skills,
)

# Minimal stand-in for configs/training/training_profiles.yaml.
PROFILES = {
    "defaults": {"unspecified_requirement": "never"},
    "requirement_rules": {
        "always": {"include_in_scoring": True, "requires_opportunity": False},
        "conditional": {"include_in_scoring": True, "requires_opportunity": True},
        "as_needed": {"include_in_scoring": True, "requires_opportunity": True},
        "never": {"include_in_scoring": False, "requires_opportunity": False},
    },
    "skill_categories": {
        "customer_connection": {"label": "Customer Connection"},
        "closing": {"label": "Closing"},
    },
    "skills": {
        "warm_greeting": {
            "label": "Warm Greeting", "category": "customer_connection",
            "behavior_names": ["greeting"], "mapping_status": "mapped",
        },
        "show_empathy": {
            "label": "Show Empathy", "category": "customer_connection",
            "behavior_names": ["show compassion"], "mapping_status": "mapped_alias",  # alias
        },
        "ask_for_sale": {
            "label": "Ask for the Sale", "category": "closing",
            "behavior_names": ["ask for sale"], "mapping_status": "mapped",
        },
        "understand_customer_issue": {
            "label": "Understand Customer Issue", "category": "customer_connection",
            "behavior_names": [], "mapping_status": "unmapped",  # never scorable
        },
    },
    "profiles": {
        # profile A: greeting always-required; ask_for_sale required; empathy NOT listed (-> never)
        "profile_a": {
            "challenge_id": "profile_a",
            "requirements": {"always": ["warm_greeting"], "conditional": ["ask_for_sale"]},
        },
        # profile B: greeting required too (shares skill with A -> aggregation across profiles)
        "profile_b": {
            "challenge_id": "profile_b",
            "requirements": {"always": ["warm_greeting", "show_empathy"]},
        },
    },
}


def _raw(agent_id, challenge, behavior, num, den, attempts=1, week="2026-08-14"):
    return {
        "agent_id": agent_id, "week_ending": week, "call_type": "all",
        "scorecard_name": challenge, "behavior": behavior,
        "attempts": attempts, "numerator": num, "denominator": den,
        "calc": (num / den if den else None),
    }


def test_behavior_skill_map_includes_alias():
    b2s = build_behavior_skill_map(PROFILES)
    assert b2s["greeting"] == "warm_greeting"
    assert b2s["show compassion"] == "show_empathy"  # alias resolves


def test_profile_requirements_exclude_unspecified():
    req = build_profile_requirements(PROFILES, {"always", "conditional", "as_needed"})
    assert req["profile_a"] == {"warm_greeting", "ask_for_sale"}
    # empathy is not listed for profile_a -> unspecified -> not counted
    assert "show_empathy" not in req["profile_a"]


def test_relevance_drop_for_unspecified_skill():
    # In profile_a, "show compassion"->show_empathy is NOT required -> dropped.
    raw = pd.DataFrame([
        _raw(1, "profile_a", "greeting", 3, 4),
        _raw(1, "profile_a", "show compassion", 1, 4),
    ])
    out = build_training_assist_skills(raw, PROFILES)
    skills = set(out["skill"])
    assert "warm_greeting" in skills
    assert "show_empathy" not in skills  # relevance filter dropped it


def test_unmapped_behavior_dropped():
    raw = pd.DataFrame([
        _raw(1, "profile_a", "greeting", 2, 2),
        _raw(1, "profile_a", "totally unknown behavior", 5, 5),
    ])
    out = build_training_assist_skills(raw, PROFILES)
    assert set(out["skill"]) == {"warm_greeting"}


def test_aggregates_same_skill_across_profiles():
    # warm_greeting is required by both profile_a and profile_b in the same week ->
    # numerator/denominator must SUM across the two profiles into one skill row.
    raw = pd.DataFrame([
        _raw(1, "profile_a", "greeting", 3, 5),
        _raw(1, "profile_b", "greeting", 4, 5),
    ])
    out = build_training_assist_skills(raw, PROFILES)
    row = out[out["skill"] == "warm_greeting"].iloc[0]
    assert row["numerator"] == 7
    assert row["denominator"] == 10
    assert abs(row["calc"] - 0.7) < 1e-9
    assert row["skill_label"] == "Warm Greeting"
    assert row["category"] == "customer_connection"


def test_conditional_skill_counts_when_behavior_present():
    # ask_for_sale is `conditional` in profile_a; a raw row exists (behavior was evaluated)
    # so requires_opportunity is already satisfied -> it should count.
    raw = pd.DataFrame([_raw(1, "profile_a", "ask for sale", 1, 2)])
    out = build_training_assist_skills(raw, PROFILES)
    assert "ask_for_sale" in set(out["skill"])


def test_empty_input_returns_empty_frame():
    out = build_training_assist_skills(pd.DataFrame(), PROFILES)
    assert out.empty
    assert "skill" in out.columns


# --------------------------------------------------------------------------- #
# ASCEND scope filter (lob == ASCEND Launchpad)
# --------------------------------------------------------------------------- #
_LOB_PROFILES = {
    **PROFILES,
    "profiles": {
        "asc_x": {"challenge_id": "asc_x", "lob": "ASCEND Launchpad",
                  "requirements": {"always": ["warm_greeting"]}},
        "verizon_y": {"challenge_id": "verizon_y", "lob": "Connected Home",
                      "requirements": {"always": ["warm_greeting"]}},
    },
}


def test_new_skills_mapped_in_real_catalog():
    # The 5 newly-added skills resolve in the real training_profiles.yaml catalog.
    from pathlib import Path
    from pde.utils.io import load_yaml
    prof = load_yaml(Path("configs/training/training_profiles.yaml"))
    b2s = build_behavior_skill_map(prof)
    assert b2s["actively listen"] == "active_listening"
    assert b2s["test troubleshooting was effective"] == "test_troubleshooting"
    assert b2s["follow the proper transfer procedures"] == "transfer_escalation"
    assert b2s["examples of coverage"] == "coverage_examples"
    assert b2s["recap"] == "recap"


def test_ascend_challenge_ids_selects_only_ascend_lob():
    assert ascend_challenge_ids(_LOB_PROFILES) == {"asc_x"}


def test_ascend_only_drops_non_ascend_but_default_keeps_all():
    raw = pd.DataFrame([
        _raw(1, "asc_x", "greeting", 3, 4),
        _raw(1, "verizon_y", "greeting", 1, 4),
    ])
    # default (off): both personas' greeting rows aggregate into the one skill
    keep_all = build_training_assist_skills(raw, _LOB_PROFILES)
    assert keep_all[keep_all["skill"] == "warm_greeting"]["numerator"].iloc[0] == 4   # 3 + 1
    # ascend_only: the non-ASCEND (verizon_y) row is dropped before rollup
    asc = build_training_assist_skills(raw, _LOB_PROFILES, ascend_only=True)
    assert asc[asc["skill"] == "warm_greeting"]["numerator"].iloc[0] == 3             # asc_x only
    assert asc["denominator"].iloc[0] == 4
