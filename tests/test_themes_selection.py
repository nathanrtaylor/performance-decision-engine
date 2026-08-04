"""Tier-2 theme qualification + ranking (src/cde/engine/themes.py)."""
import pandas as pd

from cde.engine.themes import build_theme_candidates, top_theme_per_agent

DECISION = pd.Timestamp("2026-07-31")

# Two themes: A (3 members) and B (2 members). floor=0.15, frac=0.5.
CFG = {
    "themes": {
        "themes": {
            "Theme A": {"members": ["m1", "m2", "m3"], "conversation_type": "Performance Coaching"},
            "Theme B": {"members": ["m4", "m5"], "conversation_type": "Quality Coaching"},
        }
    },
    "theme_selection": {"count_fraction": 0.5, "score_level_floor": 0.15, "aggregate": "mean"},
}


def _sw(rows):
    """rows: list of (metric, score_level). Fill the rest with sane windowed columns."""
    out = []
    for metric, lvl in rows:
        out.append({
            "agent_id": "A1", "period": DECISION, "call_type": "all", "metric": metric,
            "score_level": lvl, "score_trend": 0.1, "score_risk": 0.1,
            "score_confidence": 0.8, "score_total": lvl,  # total tracks level for easy asserts
            "benchmark_8w": 1.0, "level_8w": -0.2, "direction": "higher_is_better",
        })
    return pd.DataFrame(out)


def test_qualifies_at_half_not_below():
    # Theme A: 2 of 3 deficient (>=0.5) -> qualifies. Theme B: 0 of 2 -> no.
    sw = _sw([("m1", 0.4), ("m2", 0.3), ("m3", 0.0), ("m4", 0.0), ("m5", 0.05)])
    cands, members = build_theme_candidates(sw, CFG)
    assert set(cands["theme"]) == {"Theme A"}
    row = cands.iloc[0]
    assert row["n_members"] == 3 and row["n_deficient"] == 2
    # member detail carries only the qualifying theme's members
    assert set(members["metric"]) == {"m1", "m2", "m3"}


def test_below_half_does_not_qualify():
    # Theme A: only 1 of 3 deficient (0.33 < 0.5) -> no qualify.
    sw = _sw([("m1", 0.4), ("m2", 0.0), ("m3", 0.0)])
    cands, _ = build_theme_candidates(sw, CFG)
    assert cands.empty


def test_loose_floor_admits_subthreshold_members():
    # score_level 0.16 is below a typical solo bar but clears the loose 0.15 floor.
    sw = _sw([("m1", 0.16), ("m2", 0.16), ("m3", 0.0)])
    cands, _ = build_theme_candidates(sw, CFG)
    assert set(cands["theme"]) == {"Theme A"}


def test_combined_score_ranking_and_conversation_type():
    # Both themes qualify; Theme B has higher mean deficient score_total -> ranks first.
    sw = _sw([("m1", 0.2), ("m2", 0.2), ("m3", 0.2),   # Theme A mean 0.2
              ("m4", 0.9), ("m5", 0.9)])               # Theme B mean 0.9
    cands, _ = build_theme_candidates(sw, CFG)
    top = top_theme_per_agent(cands)
    assert len(top) == 1
    assert top.iloc[0]["theme"] == "Theme B"
    assert top.iloc[0]["conversation_type"] == "Quality Coaching"


def test_no_themes_configured_is_empty():
    sw = _sw([("m1", 0.9)])
    cands, members = build_theme_candidates(sw, {"theme_selection": {}})
    assert cands.empty and members.empty
