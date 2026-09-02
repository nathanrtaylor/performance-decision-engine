"""Composite/derived metric synthesis (src/pde/signals/derived_metrics.py)."""
import pandas as pd

from pde.signals.derived_metrics import (
    derived_component_keys,
    derived_defs,
    synthesize_derived,
)


def _cfg(metrics: dict) -> dict:
    """metric_catalog in the wrapped shape resolve_active_config produces."""
    return {"metric_catalog": {"metric_catalog": {"metrics": metrics}}}


# talk+acw: sum two numerators over a shared denominator (calls).
TALK_ACW = {
    "talk_plus_acw": {
        "source": "derived",
        "source_metric_key": "talk_plus_acw",
        "derived": {
            "numerator": [{"metric_key": "talk time"}, {"metric_key": "acw"}],
            "denominator": {"metric_key": "talk time", "part": "denominator"},
        },
    }
}

# sp100: one numerator divided by ANOTHER metric's numerator (a count).
SP100 = {
    "sp100": {
        "source": "derived",
        "source_metric_key": "sp100",
        "derived": {
            "numerator": [{"metric_key": "enrolled"}],
            "denominator": {"metric_key": "sales opportunities", "part": "numerator"},
        },
    }
}


def _row(metric, num, den, agent="a1", cohort="mob-verizon"):
    calc = (num / den) if den else 0.0
    return {
        "agent_id": agent, "period": "2026-07-24", "call_type": "all", "icp_client": cohort,
        "site": "t", "metric": metric, "numerator": num, "denominator": den, "calc": calc,
    }


def test_sum_numerators_over_shared_denominator():
    df = pd.DataFrame([_row("talk time", 300, 100), _row("acw", 50, 100)])
    out = synthesize_derived(df, _cfg(TALK_ACW), metric_key_col="metric")
    assert list(out["metric"].unique()) == ["talk_plus_acw"]
    r = out.iloc[0]
    assert r["numerator"] == 350 and r["denominator"] == 100
    assert r["calc"] == 3.5


def test_ratio_of_two_counts():
    # sp100 = enrolled.numerator / sales_opportunities.numerator = 20 / 40 = 0.5
    df = pd.DataFrame([_row("enrolled", 20, 100), _row("sales opportunities", 40, 100)])
    out = synthesize_derived(df, _cfg(SP100), metric_key_col="metric")
    r = out.iloc[0]
    assert r["numerator"] == 20 and r["denominator"] == 40
    assert r["calc"] == 0.5


def test_missing_component_skips_the_row():
    # Only talk time present (no acw) -> the whole composite is skipped for that group.
    df = pd.DataFrame([_row("talk time", 300, 100)])
    out = synthesize_derived(df, _cfg(TALK_ACW), metric_key_col="metric")
    assert out.empty


def test_zero_denominator_skipped():
    # sales opportunities numerator == 0 -> divisor invalid -> skip.
    df = pd.DataFrame([_row("enrolled", 20, 100), _row("sales opportunities", 0, 100)])
    out = synthesize_derived(df, _cfg(SP100), metric_key_col="metric")
    assert out.empty


def test_per_group_isolation():
    # Two agents; one is missing acw -> only the complete agent yields a composite.
    df = pd.DataFrame([
        _row("talk time", 300, 100, agent="a1"), _row("acw", 50, 100, agent="a1"),
        _row("talk time", 200, 100, agent="a2"),  # a2 has no acw
    ])
    out = synthesize_derived(df, _cfg(TALK_ACW), metric_key_col="metric")
    assert set(out["agent_id"]) == {"a1"}
    assert out.iloc[0]["calc"] == 3.5


def test_no_derived_defs_returns_empty():
    df = pd.DataFrame([_row("talk time", 300, 100)])
    out = synthesize_derived(df, _cfg({"talk_time": {"source": "agent_metrics", "source_metric_key": "talk time"}}), metric_key_col="metric")
    assert out.empty


def test_recalc_metric_key_column_variant():
    # The recalc path keys on 'metric' too, but the frame uses 'week_ending' as the period column.
    df = pd.DataFrame([
        {"agent_id": "a1", "week_ending": "2026-07-24", "call_type": "all", "icp_client": "xbox",
         "site": "t", "metric": "enrolled", "numerator": 10, "denominator": 100, "calc": 0.1},
        {"agent_id": "a1", "week_ending": "2026-07-24", "call_type": "all", "icp_client": "xbox",
         "site": "t", "metric": "sales opportunities", "numerator": 25, "denominator": 100, "calc": 0.25},
    ])
    out = synthesize_derived(df, _cfg(SP100), metric_key_col="metric")
    assert "week_ending" in out.columns
    assert out.iloc[0]["calc"] == 10 / 25


def _bs_config() -> dict:
    """Minimal config for build_signals: a 'derived' source binding + the sp100 derived metric."""
    return {
        "call_type_mode": "disabled",
        "default_call_type": "all",
        "source_catalog": {"source_catalog": {"sources": {
            "derived": {
                "schema": {
                    "entity_keys": {"agent_id": "agent_id", "period": "period", "call_type": "call_type"},
                    "metric_key": "metric", "numerator": "numerator", "denominator": "denominator",
                    "calculation": None, "value": "calc",
                },
                "computation": {
                    "prefer_value_column_if_present": True,
                    "calculation_handlers": {"rate": {"formula": "numerator / denominator", "denominator_min": 1}},
                },
            }
        }}},
        "metric_catalog": {"metric_catalog": {
            "metrics": {
                "sp100": {
                    "source": "derived", "source_metric_key": "sp100", "category": "sell",
                    "direction": "higher_is_better", "unit": "rate", "benchmark": {"type": "config"},
                }
            },
            "governance": {"disallow_unknown_metrics": True},
        }},
    }


def test_build_signals_values_a_derived_source_row():
    from pde.signals.build_signals import build_signals

    df = pd.DataFrame([_row("enrolled", 20, 100), _row("sales opportunities", 40, 100)])
    derived = synthesize_derived(df, _cfg(SP100), metric_key_col="metric")
    agents = pd.DataFrame([{
        "agent_id": "a1", "week_ending": "2026-07-24", "mascot": "t",
        "icp_client": "mob-verizon", "coach": "c", "coach_id": 1,
    }])
    sig = build_signals({"derived": derived, "agents": agents}, _bs_config())
    row = sig[sig["metric"] == "sp100"].iloc[0]
    assert row["value"] == 0.5
    assert row["source"] == "derived"


def test_component_match_is_case_insensitive():
    # Source strings vary in case (extract keeps original case); the ref must still match.
    df = pd.DataFrame([_row("Enrolled", 20, 100), _row("Sale_Opportunity", 40, 100)])
    spec = {
        "sp100": {
            "source": "derived", "source_metric_key": "sp100",
            "derived": {
                "numerator": [{"metric_key": "enrolled"}],
                "denominator": {"metric_key": "sale_opportunity", "part": "numerator"},
            },
        }
    }
    out = synthesize_derived(df, _cfg(spec), metric_key_col="metric")
    assert out.iloc[0]["calc"] == 0.5


def test_component_keys_and_defs():
    keys = derived_component_keys(_cfg({**TALK_ACW, **SP100}))
    assert keys == {"talk time", "acw", "enrolled", "sales opportunities"}
    defs = derived_defs(_cfg(TALK_ACW))
    assert defs["talk_plus_acw"]["denominator"] == {"metric_key": "talk time", "part": "denominator"}
    assert len(defs["talk_plus_acw"]["numerator"]) == 2
