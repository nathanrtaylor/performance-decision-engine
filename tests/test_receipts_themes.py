"""Receipts for theme + break-glass tiers (src/cde/engine/receipts.py)."""
import pandas as pd

from cde.engine.receipts import build_receipts, receipts_to_jsonl
from cde.engine.select import select_recommendations

P2 = pd.Timestamp("2026-07-31")

CFG = {
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
    "explainability": {"top_competitors": 3},
    "meta": {"version": "vTEST", "data_snapshot": "snap-1", "engine_version": "0.1.0"},
}


def _candidates(rows):
    out = []
    for agent, topic, ps in rows:
        out.append({"agent_id": agent, "period": P2, "call_type": "all",
                    "topic": topic, "priority_score": ps, "risk_score": 0.1, "confidence_score": 0.8})
    return pd.DataFrame(out)


def _sw_rows(agent, pairs):
    return [{
        "agent_id": agent, "period": P2, "call_type": "all", "metric": metric,
        "score_level": lvl, "score_trend": 0.1, "score_risk": 0.1,
        "score_confidence": 0.8, "score_total": lvl,
        "benchmark_8w": 1.0, "level_8w": -0.2, "direction": "higher_is_better",
    } for metric, lvl in pairs]


def _setup():
    cands = _candidates([
        ("BG", "Some Single Topic", 0.9),
        ("THEME", "Some Single Topic", 0.9),
        ("SINGLE", "Reduce Hold Time", 0.4),
    ])
    sw = pd.DataFrame(
        _sw_rows("BG", [("m1", 0.4), ("m2", 0.3), ("m3", 0.0)])
        + _sw_rows("THEME", [("m1", 0.4), ("m2", 0.3), ("m3", 0.0)])
        + _sw_rows("SINGLE", [("m1", 0.0), ("m2", 0.0), ("m3", 0.0)])
    )
    es_rows = [("BG", P2, "cancel_rate", 0.92, 0.12, 0.80, "lower_is_better", "mob-verizon")]
    for i in range(9):
        es_rows.append((f"f{i}", P2, "cancel_rate", 0.12 + (i - 8) * 0.01,
                        0.12, (i - 8) * 0.01, "lower_is_better", "mob-verizon"))
    es = pd.DataFrame(es_rows, columns=["agent_id", "period", "metric", "value",
                                        "benchmark", "gap", "direction", "icp_client"])
    es["call_type"] = "all"
    recs, detail = select_recommendations(cands, es, sw, CFG)
    return cands, es, sw, recs, detail


def test_receipts_cover_all_tiers_with_provenance():
    cands, es, sw, recs, detail = _setup()
    receipts = build_receipts(recs, cands, es, sw, CFG, selection_detail=detail)
    by_agent = {r["agent_id"]: r for _, r in receipts.iterrows()}

    # Theme receipt: multiple drivers + theme_membership + tier
    th = by_agent["THEME"]
    assert th["tier"] == "theme"
    assert th["recommended_topic"] == "Theme A"
    assert len(th["drivers"]) == 2  # m1, m2 deficient
    assert th["theme_membership"]["n_deficient"] == 2
    assert th["theme_membership"]["n_members"] == 3
    assert set(th["theme_membership"]["deficient_metrics"]) == {"m1", "m2"}

    # Break-glass receipt: override + reason + cohort_pct on driver
    bg = by_agent["BG"]
    assert bg["tier"] == "break_glass"
    assert bg["override"] is True and bg["reason"] == "break_glass"
    assert bg["recommended_topic"] == "Reduce Cancel Rate"
    assert bg["drivers"][0]["cohort_pct"] is not None

    # Single receipt: unchanged shape (single driver + competing_topics key)
    sg = by_agent["SINGLE"]
    assert sg["tier"] == "single"
    assert len(sg["drivers"]) == 1
    assert "competing_topics" in sg

    # provenance present on every tier
    for r in (th, bg, sg):
        assert r["provenance"]["config_version"] == "vTEST"

    # serializes cleanly to JSONL
    text = receipts_to_jsonl(receipts)
    assert text.count("\n") == len(receipts)


def test_abstention_receipts_appended():
    cands, es, sw, recs, detail = _setup()
    abstentions = pd.DataFrame([
        {"agent_id": "Z1", "period": P2, "call_type": "all", "reason": "below_coaching_floor",
         "best_topic": "Reduce Talk Time", "best_priority_score": 0.02, "best_level_score": 0.1},
        {"agent_id": "Z2", "period": P2, "call_type": "all", "reason": "no_qualified_signal",
         "best_topic": None, "best_priority_score": None, "best_level_score": None},
    ])
    receipts = build_receipts(recs, cands, es, sw, CFG, selection_detail=detail, abstentions=abstentions)
    by_agent = {r["agent_id"]: r for _, r in receipts.iterrows()}

    z1 = by_agent["Z1"]
    assert z1["tier"] == "abstained"
    assert z1["reason"] == "below_coaching_floor"
    assert pd.isna(z1["recommended_topic"])  # None coerces to NaN in the frame; JSONL emits null
    assert z1["drivers"][0]["topic"] == "Reduce Talk Time"
    assert z1["provenance"]["config_version"] == "vTEST"

    z2 = by_agent["Z2"]
    assert z2["tier"] == "abstained" and z2["reason"] == "no_qualified_signal"
    assert z2["drivers"] == []  # no best-available topic
