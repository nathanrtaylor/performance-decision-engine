"""Unit tests for receipt narrative templates (src/cde/explainability/templates.py)."""
import pandas as pd

from cde.explainability.templates import (
    _percentile_band,
    _trend_phrase,
    narrative_why_now,
    narrative_why_not,
    narrative_theme_why_not,
    narrative_abstention,
)

_INTERNAL_TOKENS = ("priority_score", "risk_score", "trend_score", "level_score", "confidence_score")


def _no_internal_scores(text: str) -> bool:
    return not any(tok in text for tok in _INTERNAL_TOKENS)


# --- _percentile_band -------------------------------------------------------
def test_percentile_band_boundaries():
    assert _percentile_band(0.45) == "far below the benchmark standing among peers"
    assert _percentile_band(0.40) == "far below the benchmark standing among peers"
    assert _percentile_band(0.25) == "well below the benchmark standing among peers"
    assert _percentile_band(0.20) == "well below the benchmark standing among peers"
    assert _percentile_band(0.12) == "modestly below the benchmark standing among peers"
    assert _percentile_band(0.10) == "modestly below the benchmark standing among peers"
    assert _percentile_band(0.05) == "slightly below the benchmark standing among peers"
    assert _percentile_band(0.0) == "about at the benchmark standing among peers"
    assert _percentile_band(None) == "about at the benchmark standing among peers"


# --- _trend_phrase (direction-aware) ---------------------------------------
def test_trend_phrase_direction_awareness():
    # Same raw slope sign reads oppositely depending on metric direction.
    lower_worse = _trend_phrase(0.03, 0.02, "lower_is_better")   # rising gap = worsening
    higher_ok = _trend_phrase(0.03, 0.02, "higher_is_better")    # rising gap = improving
    assert "widening" in lower_worse and "recent weeks are the worst" in lower_worse
    assert "improving" in higher_ok


def test_trend_phrase_widening_without_recency():
    txt = _trend_phrase(0.03, -0.01, "lower_is_better")
    assert "widening" in txt and "recent weeks" not in txt


def test_trend_phrase_steady_when_missing_or_flat():
    assert "held roughly steady" in _trend_phrase(None, None, "lower_is_better")
    assert "held roughly steady" in _trend_phrase(0.0, 0.0, "higher_is_better")


# --- narrative_why_now ------------------------------------------------------
def test_why_now_worsening_no_internal_scores():
    row = pd.Series({"metric": "fcr", "value": 0.61, "benchmark": 0.78, "level_score": 0.25})
    txt = narrative_why_now(row, trend_8w=-0.03, recency_shift=-0.02, direction="higher_is_better")
    assert "fcr" in txt
    assert "0.610" in txt and "0.780" in txt          # value vs benchmark shown
    assert "well below the benchmark standing among peers" in txt
    assert "widening" in txt                            # falling gap on higher_is_better = worsening
    assert _no_internal_scores(txt)


def test_why_now_improving_branch():
    row = pd.Series({"metric": "aht", "value": 5.2, "benchmark": 4.0, "level_score": 0.12})
    txt = narrative_why_now(row, trend_8w=-0.03, recency_shift=-0.01, direction="lower_is_better")
    assert "improving" in txt
    assert _no_internal_scores(txt)


def test_why_now_steady_branch():
    row = pd.Series({"metric": "aht", "value": 4.1, "benchmark": 4.0, "level_score": 0.05})
    txt = narrative_why_now(row, trend_8w=None, recency_shift=None, direction="lower_is_better")
    assert "held roughly steady" in txt
    assert _no_internal_scores(txt)


# --- narrative_why_not (single) --------------------------------------------
def test_why_not_with_competitor():
    comps = [{"topic": "Reduce Hold Time", "metric": "avg_hold",
              "reason_not_selected": "the chosen topic sits further below its benchmark relative to peers"}]
    txt = narrative_why_not(comps)
    assert "Reduce Hold Time" in txt and "avg_hold" in txt
    assert _no_internal_scores(txt)


def test_why_not_empty():
    txt = narrative_why_not([])
    assert "No other eligible topics" in txt


# --- narrative_theme_why_not -----------------------------------------------
def test_theme_why_not_with_displaced_single():
    txt = narrative_theme_why_not("Call Control", 3, 4, alt_topic="Reduce Hold Time", alt_metric="avg_hold")
    assert "Call Control" in txt
    assert "Reduce Hold Time" in txt and "avg_hold" in txt
    assert "3 of 4" in txt and "together" in txt
    assert _no_internal_scores(txt)


def test_theme_why_not_without_alt():
    txt = narrative_theme_why_not("Call Control", 3, 4, alt_topic=None)
    assert "several" in txt and "together" in txt
    assert _no_internal_scores(txt)


def test_theme_why_not_alt_nan():
    # NaN alt_topic (non-theme merge miss) falls back to the generic pattern sentence.
    txt = narrative_theme_why_not("Call Control", 3, 4, alt_topic=float("nan"))
    assert "several" in txt


# --- narrative_abstention ---------------------------------------------------
def test_abstention_drops_priority_score():
    txt = narrative_abstention("below_coaching_floor", best_topic="Reduce Talk Time", best_priority_score=0.02)
    assert "Reduce Talk Time" in txt
    assert "0.02" not in txt
    assert "priority_score" not in txt
