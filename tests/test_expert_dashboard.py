"""Expert dashboard: recent-coaching-history block (src/cde/reporting/expert_dashboard.py)."""
import json
import re

import pandas as pd

from cde.reporting.expert_dashboard import (
    build_experts,
    coaching_history_map_from_df,
    render_html,
    write_expert_dashboard,
)


def _coaching_history():
    # Agent 100 has 6 events (tests the 5-cap + newest-first ordering); agent 200 has 1.
    cols = ["agent_id", "coaching_date", "coaching_status", "coaching_type",
            "coaching_topic", "behavior", "behavior_selected", "expert_acknowledged"]
    return pd.DataFrame([
        ["100", "2026-06-01", "Submitted", "ADAPT", "", "Confidence", "Confidence", "1"],
        ["100", "2026-07-22", "Submitted", "In The Game", "Accountability", "Drive Results", "Drive Results", "1"],
        ["100", "2026-07-01", "Excused", "Growth Plan", "", "Talk Time", "Talk Time", "1"],
        ["100", "2026-08-10", "Submitted", "ADAPT", "", "Empathy", "Show Compassion", "1"],
        ["100", "2026-05-01", "Submitted", "ITG", "", "Oldest", "Oldest", "1"],
        ["100", "2026-08-15", "Submitted", "Growth Plan", "", "Newest", "Newest", "1"],
        ["200", "2026-08-02", "Submitted", "ADAPT", "", "Verify", "Verification", "1"],
    ], columns=cols)


def _receipts():
    def r(aid):
        return {
            "agent_id": aid, "tier": "single", "recommended_topic": "Reduce Talk Time",
            "conversation_type": "claims",
            "narrative": {"why_this": "a", "why_now": "b", "why_not_others": "c"},
            "drivers": [], "core_metrics": {"period": "2026-08-14", "as_of_latest": True, "metrics": []},
            "provenance": {"config_version": "v1"},
        }
    # 300 has no coaching history -> its block must be hidden.
    return [r("100"), r("300")]


def _agents():
    return pd.DataFrame([
        {"agent_id": "100", "week_ending": "2026-08-14", "icp_client": "mob-verizon",
         "mascot": "falcons", "coach": "Coach A", "agent_name": "Alex"},
        {"agent_id": "300", "week_ending": "2026-08-14", "icp_client": "mob-verizon",
         "mascot": "falcons", "coach": "Coach A", "agent_name": "Sam"},
    ])


def test_map_caps_at_five_newest_first():
    m = coaching_history_map_from_df(_coaching_history())
    ev = m["100"]
    assert len(ev) == 5                                  # oldest of the 6 dropped
    dates = [e["dt"] for e in ev]
    assert dates == sorted(dates, reverse=True)
    assert dates[0] == "2026-08-15" and "2026-05-01" not in dates


def test_map_prefers_behavior_selected_and_keeps_type_status():
    m = coaching_history_map_from_df(_coaching_history())
    row = next(e for e in m["100"] if e["dt"] == "2026-08-10")
    assert row == {"ty": "ADAPT", "tp": "Show Compassion", "dt": "2026-08-10", "st": "Submitted"}


def test_map_handles_none_and_missing_columns():
    assert coaching_history_map_from_df(None) == {}
    assert coaching_history_map_from_df(pd.DataFrame()) == {}
    assert coaching_history_map_from_df(pd.DataFrame({"foo": [1]})) == {}


def test_build_experts_attaches_history_and_hides_when_absent():
    amap = {"100": {"icp": "mob-verizon", "mascot": "falcons", "coach": "", "name": "Alex"},
            "300": {"icp": "mob-verizon", "mascot": "falcons", "coach": "", "name": "Sam"}}
    chmap = coaching_history_map_from_df(_coaching_history())
    by = {e["id"]: e for e in build_experts(_receipts(), amap, chmap)}
    assert len(by["100"]["hist"]) == 5
    assert by["300"]["hist"] == []                       # no history -> empty (block hidden client-side)


def test_build_experts_backward_compatible_without_chmap():
    amap = {"100": {"icp": "x", "mascot": "y", "coach": "", "name": ""}}
    experts = build_experts([_receipts()[0]], amap)      # chmap omitted
    assert experts[0]["hist"] == []


def test_render_embeds_history_block_and_helpers():
    experts = build_experts(_receipts(),
                            {a: {"icp": "mob-verizon", "mascot": "falcons", "coach": "", "name": ""}
                             for a in ("100", "300")},
                            coaching_history_map_from_df(_coaching_history()))
    h = render_html(experts, {"run_id": "t"})
    assert "Recent coaching history" in h                # section header
    assert "function coachingHistoryTable" in h          # table builder
    assert 'key==="hist"' in h                           # copy branch (else copy button throws)
    data = json.loads(re.search(
        r'<script id="experts" type="application/json">(.*?)</script>', h, re.S).group(1))
    assert len(next(e for e in data if e["id"] == "100")["hist"]) == 5


def test_write_expert_dashboard_with_history(tmp_path):
    out = tmp_path / "expert_dashboard.html"
    stats = write_expert_dashboard(out, _receipts(), _agents(), {"run_id": "t"},
                                   coaching_history=_coaching_history())
    assert stats["experts"] == 2
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html")
    assert "Recent coaching history" in html
