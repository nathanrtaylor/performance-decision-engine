"""HTML run dashboard (src/cde/reporting/dashboard.py)."""
import pandas as pd

from cde.reporting.dashboard import build_dashboard_html, write_dashboard

CFG = {
    "meta": {"data_snapshot": "SNAP1", "version": "v1", "engine_version": "0.1.0"},
    "metric_catalog": {"metric_catalog": {"metrics": {
        "transfer_rate": {"direction": "lower_is_better"},
        "nsp100": {"direction": "higher_is_better"},
    }}},
}


def _recs():
    return pd.DataFrame([
        {"agent_id": "1", "topic": "Reduce Client Transfer Rate",
         "conversation_type": "Performance Correction", "priority_score": 0.5, "metric": "transfer_rate"},
        {"agent_id": "2", "topic": "Reduce Client Transfer Rate",
         "conversation_type": "Performance Correction", "priority_score": 0.4, "metric": "transfer_rate"},
        {"agent_id": "3", "topic": "Improve NSP100",
         "conversation_type": "Performance Coaching", "priority_score": 0.3, "metric": "nsp100"},
    ])


def _agents():
    return pd.DataFrame([
        {"agent_id": "1", "week_ending": "2026-06-19", "icp_client": "pss-verizon", "mascot": "hawks"},
        {"agent_id": "2", "week_ending": "2026-06-19", "icp_client": "pss-at&t", "mascot": "tigers"},
        {"agent_id": "3", "week_ending": "2026-06-19", "icp_client": "pss-verizon", "mascot": "hawks"},
    ])


def _signals():
    rows = [
        {"agent_id": "1", "value": 0.20, "gap": 0.08},   # worse than benchmark
        {"agent_id": "2", "value": 0.05, "gap": -0.07},  # better
        {"agent_id": "3", "value": 0.02, "gap": -0.10},  # better
    ]
    df = pd.DataFrame(rows)
    df["period"] = "2026-06-19"
    df["metric"] = "transfer_rate"
    df["benchmark"] = 0.12
    df["direction"] = "lower_is_better"
    df["denominator"] = 30
    return df


def test_core_sections_present():
    h = build_dashboard_html(_recs(), _signals(), CFG, agents=_agents())
    for s in ["Recommendations by topic", "Split by icp_client", "Split by mascot",
              "Metric health", "Data issues"]:
        assert s in h


def test_splits_populate_from_agents_not_unknown():
    h = build_dashboard_html(_recs(), _signals(), CFG, agents=_agents())
    icp_block = h.split("Split by icp_client")[1].split("Split by mascot")[0]
    assert "pss-verizon" in icp_block
    assert "pss-at&amp;t" in icp_block  # ampersand escaped
    mascot_block = h.split("Split by mascot")[1].split("Metric health")[0]
    assert "hawks" in mascot_block


def test_metric_health_status_chip_has_label_not_color_only():
    h = build_dashboard_html(_recs(), _signals(), CFG, agents=_agents())
    # status is icon + text label (accessibility): a chip class plus a word must appear
    assert 'class="chip' in h
    assert ("Healthy" in h or "deficit" in h or "misconfig" in h or "null" in h)


def test_mixed_period_dtypes_do_not_crash():
    # In the real pipeline the full signals frame mixes datetime (agent_metrics) and str
    # (behavior_scores) period values; the dashboard must coerce before sorting/comparing.
    sig = _signals()
    sig["period"] = [pd.Timestamp("2026-06-19"), "2026-06-19", pd.Timestamp("2026-06-12")]
    h = build_dashboard_html(_recs(), sig, CFG, agents=_agents())
    assert "Metric health" in h and "Recommendations by topic" in h


def test_empty_recommendations_is_safe():
    h = build_dashboard_html(pd.DataFrame(), None, CFG)
    assert "No recommendations" in h
    assert h.startswith("<!doctype html")


DAMP_CFG = {**CFG, "dampening": {"mode": "multiply", "periods": 2, "multiplier": 0.5}}


def _candidates_multiply():
    # Agent A: dampened topic (post .4 -> pre .8) loses to a .5 topic  => recommendation FLIPS
    # Agent B: dampened topic (post .9 -> pre 1.8) still wins           => STILL #1 despite dampening
    return pd.DataFrame([
        {"agent_id": "A", "period": "2026-06-19", "call_type": "all",
         "topic": "Reduce Talk Time", "priority_score": 0.4, "dampened": True},
        {"agent_id": "A", "period": "2026-06-19", "call_type": "all",
         "topic": "Improve Resolution Rate", "priority_score": 0.5, "dampened": False},
        {"agent_id": "B", "period": "2026-06-19", "call_type": "all",
         "topic": "Reduce Talk Time", "priority_score": 0.9, "dampened": True},
        {"agent_id": "B", "period": "2026-06-19", "call_type": "all",
         "topic": "Reduce Hold Time", "priority_score": 0.3, "dampened": False},
    ])


def test_dampening_section_impact_and_rate():
    h = build_dashboard_html(_recs(), _signals(), DAMP_CFG,
                             candidates=_candidates_multiply(), agents=_agents())
    assert "Recency dampening" in h
    assert "multiply" in h and "<b>2</b> week" in h          # mechanism line
    assert "changed <b>1</b> of 2" in h                       # 1 of 2 recommendations flipped
    assert "Recommendations changed" in h                     # impact tile
    assert "Still #1 despite dampening" in h                  # severe-enough tile
    damp_block = h.split("Recency dampening")[1]
    assert "Reduce Talk Time" in damp_block
    assert "100" in damp_block                                # rate = 100% of that topic's candidates


def test_dampening_suppress_mode_shows_note_and_omits_impact():
    cfg = {**CFG, "dampening": {"mode": "suppress", "periods": 2}}
    cand = _candidates_multiply().assign(dampened=False)  # suppressed rows aren't retained
    h = build_dashboard_html(_recs(), _signals(), cfg, candidates=cand, agents=_agents())
    assert "Recency dampening" in h
    assert "suppress" in h
    assert "Recommendations changed" not in h            # impact not reconstructable in suppress mode


def test_dampening_section_omitted_without_dampened_column():
    cand = _candidates_multiply().drop(columns=["dampened"])
    h = build_dashboard_html(_recs(), _signals(), DAMP_CFG, candidates=cand, agents=_agents())
    assert "Recency dampening" not in h


def test_write_dashboard_file(tmp_path):
    p = write_dashboard(tmp_path / "d.html", recommendations=_recs(),
                        signals=_signals(), config=CFG, agents=_agents())
    assert p.exists()
    assert p.read_text(encoding="utf-8").startswith("<!doctype html")
