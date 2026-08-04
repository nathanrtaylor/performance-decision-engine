"""Three-tier precedence + backward-compat golden test (src/cde/engine/select.py)."""
import pandas as pd

from cde.engine.recommend import recommend_for_population
from cde.engine.select import select_recommendations

P2 = pd.Timestamp("2026-07-31")


def _candidates(rows):
    """rows: (agent, topic, priority_score). Adds the columns deterministic_sort needs."""
    out = []
    for agent, topic, ps in rows:
        out.append({
            "agent_id": agent, "period": P2, "call_type": "all",
            "topic": topic, "priority_score": ps,
            "risk_score": 0.1, "confidence_score": 0.8,
        })
    return pd.DataFrame(out)


def _empty_es():
    return pd.DataFrame(columns=["agent_id", "period", "call_type", "metric", "gap", "direction", "icp_client"])


def _empty_sw():
    return pd.DataFrame(columns=["agent_id", "period", "call_type", "metric", "score_level", "score_total"])


# ---------------------------------------------------------------------------
# Golden: with no themes and no break_glass flags, selection == the single argmax.
# ---------------------------------------------------------------------------
def test_no_themes_no_flags_equals_single_argmax():
    cands = _candidates([
        ("a1", "Reduce Hold Time", 0.5),
        ("a1", "Improve NSP100", 0.3),
        ("a2", "Reduce Talk Time", 0.4),
        ("a2", "Reduce CRT", 0.2),
    ])
    cfg = {}  # no themes, no metric_catalog break_glass flags

    recs, detail = select_recommendations(cands, _empty_es(), _empty_sw(), cfg)
    expected = recommend_for_population(cands, cfg)

    assert (recs["tier"] == "single").all()
    assert detail.empty
    got = dict(zip(recs["agent_id"], recs["topic"]))
    exp = dict(zip(expected["agent_id"], expected["topic"]))
    assert got == exp == {"a1": "Reduce Hold Time", "a2": "Reduce Talk Time"}
    # one recommendation per agent
    assert recs.groupby(["agent_id", "period", "call_type"]).size().max() == 1


# ---------------------------------------------------------------------------
# Combined precedence: break_glass > theme > single.
# ---------------------------------------------------------------------------
_CFG = {
    "themes": {"themes": {
        "Theme A": {"members": ["m1", "m2", "m3"], "conversation_type": "Performance Coaching"},
    }},
    "theme_selection": {"count_fraction": 0.5, "score_level_floor": 0.15, "aggregate": "mean"},
    "break_glass": {"recency_weeks": 2, "worst_pct": 20},
    "metric_catalog": {"metric_catalog": {"metrics": {
        "cancel_rate": {"direction": "lower_is_better", "break_glass": {"enabled": True, "worst_pct": 20}},
    }}},
    "topic_map": {"topic_map": {
        "metric_to_topic": {"cancel_rate": "Reduce Cancel Rate"},
        "topic_to_conversation_type": {"Reduce Cancel Rate": "Performance Correction"},
    }},
    "conversation_types": {"default": "Performance Coaching"},
}


def _sw_rows(agent, pairs):
    rows = []
    for metric, lvl in pairs:
        rows.append({
            "agent_id": agent, "period": P2, "call_type": "all", "metric": metric,
            "score_level": lvl, "score_trend": 0.1, "score_risk": 0.1,
            "score_confidence": 0.8, "score_total": lvl,
            "benchmark_8w": 1.0, "level_8w": -0.2, "direction": "higher_is_better",
        })
    return rows


def test_break_glass_overrides_theme_overrides_single():
    # candidates: every agent has a fallback single topic.
    cands = _candidates([
        ("BG", "Some Single Topic", 0.9),
        ("THEME", "Some Single Topic", 0.9),
        ("SINGLE", "Reduce Hold Time", 0.4),
    ])

    # scores_windowed: BG and THEME both qualify Theme A (2/3 deficient); SINGLE does not.
    sw = pd.DataFrame(
        _sw_rows("BG", [("m1", 0.4), ("m2", 0.3), ("m3", 0.0)])
        + _sw_rows("THEME", [("m1", 0.4), ("m2", 0.3), ("m3", 0.0)])
        + _sw_rows("SINGLE", [("m1", 0.0), ("m2", 0.0), ("m3", 0.0)])
    )

    # eligible_signals: cancel_rate cohort; only BG is worst-tail AND above benchmark.
    es_rows = [("BG", P2, "cancel_rate", 0.92, 0.12, 0.80, "lower_is_better", "mob-verizon")]
    for i in range(9):
        es_rows.append((f"f{i}", P2, "cancel_rate", 0.12 + (i - 8) * 0.01,
                        0.12, (i - 8) * 0.01, "lower_is_better", "mob-verizon"))
    es = pd.DataFrame(es_rows, columns=["agent_id", "period", "metric", "value",
                                        "benchmark", "gap", "direction", "icp_client"])
    es["call_type"] = "all"

    recs, detail = select_recommendations(cands, es, sw, _CFG)
    tier = dict(zip(recs["agent_id"], recs["tier"]))
    topic = dict(zip(recs["agent_id"], recs["topic"]))

    assert tier["BG"] == "break_glass"
    assert topic["BG"] == "Reduce Cancel Rate"
    assert tier["THEME"] == "theme"
    assert topic["THEME"] == "Theme A"
    assert tier["SINGLE"] == "single"
    assert topic["SINGLE"] == "Reduce Hold Time"

    # exactly one rec per real agent (filler cohort agents don't trip -> not in recs)
    assert set(recs["agent_id"]) == {"BG", "THEME", "SINGLE"}
    assert recs.groupby(["agent_id", "period", "call_type"]).size().max() == 1

    # selection_detail carries theme members for THEME and the break-glass row for BG
    assert set(detail["kind"]) == {"theme", "break_glass"}
