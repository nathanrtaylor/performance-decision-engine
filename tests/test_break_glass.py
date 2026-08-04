"""Tier-1 break-glass override (src/cde/engine/break_glass.py)."""
import pandas as pd

from cde.engine.break_glass import detect_break_glass, top_break_glass_per_agent

P1 = pd.Timestamp("2026-07-24")
P2 = pd.Timestamp("2026-07-31")

# cancel_rate is break-glass flagged (worst 20%); resolution_rate is not flagged.
CFG = {
    "break_glass": {"recency_weeks": 2, "worst_pct": 20},
    "metric_catalog": {"metric_catalog": {"metrics": {
        "cancel_rate": {"direction": "lower_is_better", "break_glass": {"enabled": True, "worst_pct": 20}},
        "resolution_rate": {"direction": "higher_is_better"},
    }}},
    "topic_map": {"topic_map": {
        "metric_to_topic": {"cancel_rate": "Reduce Cancel Rate"},
        "topic_to_conversation_type": {"Reduce Cancel Rate": "Performance Correction"},
    }},
    "conversation_types": {"default": "Performance Coaching"},
}


def _es(rows):
    """rows: (agent, period, metric, value, benchmark, gap, direction, icp_client)."""
    cols = ["agent_id", "period", "metric", "value", "benchmark", "gap", "direction", "icp_client"]
    df = pd.DataFrame(rows, columns=cols)
    df["call_type"] = "all"
    return df


def _cohort(metric, direction, benchmark, agent_gap_pairs, period=P2):
    """Build a one-cohort frame: agent_gap_pairs=[(agent, gap), ...]."""
    rows = []
    for agent, gap in agent_gap_pairs:
        rows.append((agent, period, metric, benchmark + gap, benchmark, gap, direction, "mob-verizon"))
    return _es(rows)


def test_no_flagged_metrics_returns_empty():
    # resolution_rate is NOT flagged -> nothing to override.
    es = _cohort("resolution_rate", "higher_is_better", 0.8, [("a", -0.3), ("b", -0.1)])
    cfg = dict(CFG)
    cfg = {**CFG, "metric_catalog": {"metric_catalog": {"metrics": {"resolution_rate": {"direction": "higher_is_better"}}}}}
    assert detect_break_glass(es, cfg).empty


def test_worst_tail_and_below_benchmark_trips():
    # 10 agents; worst 20% = top 2 by badness. cancel_rate lower_is_better: higher gap = worse.
    pairs = [(f"a{i}", (i - 5) * 0.02) for i in range(10)]  # gaps from -0.10..+0.08
    es = _cohort("cancel_rate", "lower_is_better", 0.12, pairs)
    bg = detect_break_glass(es, CFG)
    # Only agents above benchmark (gap>0) AND in worst 20% should trip.
    tripped = set(bg["agent_id"])
    assert "a9" in tripped  # worst
    assert "a0" not in tripped  # best (well below benchmark, gap negative)
    assert all(bg["bad_gap"] > 0)


def test_below_benchmark_required_even_if_worst():
    # Whole cohort is BELOW benchmark (all gaps negative) -> worst-of-good, must not trip.
    pairs = [(f"a{i}", -0.20 + i * 0.01) for i in range(10)]  # all negative
    es = _cohort("cancel_rate", "lower_is_better", 0.12, pairs)
    assert detect_break_glass(es, CFG).empty


def test_recency_slice_limits_to_latest_weeks():
    # Old week has an extreme agent; recency_weeks=1 should ignore P1 and only use P2.
    cfg = {**CFG, "break_glass": {"recency_weeks": 1, "worst_pct": 20}}
    old = _cohort("cancel_rate", "lower_is_better", 0.12,
                  [("a", 0.9)] + [(f"b{i}", -0.1) for i in range(9)], period=P1)
    new = _cohort("cancel_rate", "lower_is_better", 0.12,
                  [("a", -0.1)] + [(f"b{i}", 0.02 * (i - 4)) for i in range(9)], period=P2)
    es = pd.concat([old, new], ignore_index=True)
    bg = detect_break_glass(es, cfg)
    # 'a' was extreme only in the OLD week; with recency_weeks=1 it must not trip on P2.
    assert "a" not in set(bg["agent_id"])
    assert set(bg["period"].unique()) == {P2}


def test_highest_severity_selected_per_agent():
    pairs = [(f"a{i}", (i - 5) * 0.02) for i in range(10)]
    es = _cohort("cancel_rate", "lower_is_better", 0.12, pairs)
    bg = detect_break_glass(es, CFG)
    top = top_break_glass_per_agent(bg)
    # one row per agent, and topic/conversation_type resolved
    assert top.groupby(["agent_id", "period", "call_type"]).size().max() == 1
    assert set(top["topic"]) == {"Reduce Cancel Rate"}
    assert set(top["conversation_type"]) == {"Performance Correction"}
