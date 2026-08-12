from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

# Trend/recency slopes below this magnitude are treated as "flat".
_EPS = 1e-6


# ---------------------------------------------------------------------------
# why this
# ---------------------------------------------------------------------------
def narrative_why_this(rec_row: pd.Series) -> str:
    metric = rec_row.get("metric")
    value = rec_row.get("value")
    benchmark = rec_row.get("benchmark")
    band = _percentile_band(rec_row.get("level_score"))
    return (
        f"Selected because '{metric}' is the strongest opportunity for the chosen topic: "
        f"it averages {_fmt(value)} against a benchmark of {_fmt(benchmark)}, leaving this "
        f"agent {band}."
    )


# ---------------------------------------------------------------------------
# why now  (trend + percentile standing + gap; no internal scores)
# ---------------------------------------------------------------------------
def narrative_why_now(
    rec_row: pd.Series,
    trend_8w: Any = None,
    recency_shift: Any = None,
    direction: Any = None,
) -> str:
    metric = rec_row.get("metric")
    value = rec_row.get("value")
    benchmark = rec_row.get("benchmark")
    band = _percentile_band(rec_row.get("level_score"))
    return (
        f"Right now, {metric} averages {_fmt(value)} against a benchmark of "
        f"{_fmt(benchmark)}, leaving this agent {band}. "
        f"{_trend_phrase(trend_8w, recency_shift, direction)}"
    )


# ---------------------------------------------------------------------------
# why not others  (single tier)
# ---------------------------------------------------------------------------
def narrative_why_not(competitors: List[Dict[str, Any]]) -> str:
    if not competitors:
        return "No other eligible topics cleared the coaching thresholds this cycle."
    top = competitors[0]
    drv = f" (driver {top.get('metric')})" if top.get("metric") else ""
    return (
        f"The next-best alternative was '{top.get('topic')}'{drv}. It was not chosen "
        f"because {top.get('reason_not_selected')}."
    )


# ---------------------------------------------------------------------------
# theme tier
# ---------------------------------------------------------------------------
def narrative_theme_why_this(theme: str, drivers: List[Dict[str, Any]], n_deficient: int, n_members: int) -> str:
    metrics = ", ".join(str(d.get("metric")) for d in drivers) if drivers else "several behaviors"
    return (
        f"Coaching theme '{theme}' selected: {n_deficient} of {n_members} member behaviors are "
        f"deficient together ({metrics}), a pattern rather than a single issue."
    )


def narrative_theme_why_now(drivers: List[Dict[str, Any]]) -> str:
    if not drivers:
        return "Recommended now because multiple related behaviors are underperforming concurrently."
    # Rank by percentile deficit (internal, not printed) to name the most pronounced member.
    worst = max(drivers, key=lambda d: _fmt_num(d.get("level_score")))
    band = _percentile_band(worst.get("level_score"))
    trend = _trend_phrase(worst.get("trend_8w"), worst.get("recency_shift"), worst.get("direction"))
    return (
        f"Recommended now: {len(drivers)} related behaviors are underperforming together. "
        f"The most pronounced, {worst.get('metric')}, averages {_fmt(worst.get('value'))} "
        f"against a benchmark of {_fmt(worst.get('benchmark'))}, leaving it {band}. {trend}"
    )


def narrative_theme_why_not(
    theme: str,
    n_deficient: int,
    n_members: int,
    alt_topic: Any = None,
    alt_metric: Any = None,
) -> str:
    if _is_missing(alt_topic):
        return (
            "A coaching theme was preferred over any single-behavior alternative because several "
            "related behaviors are deficient together, making the shared pattern higher-leverage "
            "than any one isolated gap."
        )
    drv = f" (driver {alt_metric})" if not _is_missing(alt_metric) else ""
    return (
        f"'{theme}' was chosen over the single strongest alternative '{alt_topic}'{drv}: "
        f"{n_deficient} of {n_members} related behaviors are underperforming together, so "
        f"addressing the shared pattern is higher-leverage than coaching one isolated gap."
    )


# ---------------------------------------------------------------------------
# abstention
# ---------------------------------------------------------------------------
def narrative_abstention(reason: str, best_topic: Any = None, best_priority_score: Any = None) -> str:
    if reason == "below_coaching_floor":
        topic = best_topic or "the strongest available topic"
        return (
            f"No coaching recommended this cycle: the best available opportunity ({topic}) is "
            f"still within an acceptable range of its benchmark, so no behavior clears the "
            f"coaching floor — the agent is performing adequately relative to peers."
        )
    if reason == "no_qualified_signal":
        return (
            "No coaching recommended this cycle: no metric produced a trustworthy, benchmarked signal "
            "(insufficient volume, confidence, or reference point under production gating)."
        )
    return "No coaching recommended this cycle."


# ---------------------------------------------------------------------------
# break-glass
# ---------------------------------------------------------------------------
def narrative_break_glass(row: Any) -> str:
    metric = row.get("metric")
    cohort_pct = row.get("cohort_pct")
    value = row.get("value")
    benchmark = row.get("benchmark")
    pct_txt = _cohort_pct_text(cohort_pct)
    return (
        f"Break-glass override: agent is in the worst cohort tail ({pct_txt}) of its ICP_Client "
        f"group for '{metric}' and below benchmark (value={_fmt(value)}, benchmark={_fmt(benchmark)}). "
        f"This critical single behavior takes precedence over any theme."
    )


def narrative_break_glass_why_now(
    row: Any,
    trend_8w: Any = None,
    recency_shift: Any = None,
    direction: Any = None,
) -> str:
    metric = row.get("metric")
    value = row.get("value")
    benchmark = row.get("benchmark")
    pct_txt = _cohort_pct_text(row.get("cohort_pct"))
    base = (
        f"A flagged critical metric is severely deficient right now: {metric} sits at "
        f"{_fmt(value)} against a benchmark of {_fmt(benchmark)}, in the worst cohort tail "
        f"({pct_txt}) of its ICP_Client group."
    )
    # Only append a trend clause when we actually have trend data for this metric.
    if trend_8w is not None or recency_shift is not None:
        return f"{base} {_trend_phrase(trend_8w, recency_shift, direction)}"
    return base


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _percentile_band(level_score: Any) -> str:
    """Plain-language band for score_level (percentile deficit vs the benchmark's peer standing)."""
    v = _fmt_num(level_score)
    if v >= 0.40:
        return "far below the benchmark standing among peers"
    if v >= 0.20:
        return "well below the benchmark standing among peers"
    if v >= 0.10:
        return "modestly below the benchmark standing among peers"
    if v > 0.0:
        return "slightly below the benchmark standing among peers"
    return "about at the benchmark standing among peers"


def _trend_phrase(trend_8w: Any, recency_shift: Any, direction: Any) -> str:
    """Direction-aware trend narrative from the raw signed-gap slope and recency shift.

    ``trend_8w``/``recency_shift`` are slopes of the signed gap (value - benchmark). We convert
    to a "worsening" convention: for lower_is_better metrics a rising gap is worsening; for
    higher_is_better metrics a falling gap is worsening. ``>0`` means getting worse.
    """
    lower = str(direction) == "lower_is_better"

    def _adj(x: Any):
        v = _fmt_num(x, default=None)
        if v is None:
            return None
        return v if lower else -v

    w = _adj(trend_8w)
    r = _adj(recency_shift)

    if w is not None and w > _EPS:
        if r is not None and r > _EPS:
            return (
                "The gap has been widening over the window and the most recent weeks are the "
                "worst, so acting now prevents further slippage."
            )
        return "The gap has been widening over the window, so it is timely to intervene."
    if w is not None and w < -_EPS:
        return "The trend is improving, but the current level still warrants a nudge to lock in the gains."
    return "The level has held roughly steady over the window at a coachable gap."


def _is_missing(x: Any) -> bool:
    """True for None / NaN / pd.NA / empty-string, safely (avoids pd.NA boolean ambiguity)."""
    if x is None:
        return True
    try:
        if pd.isna(x):
            return True
    except (TypeError, ValueError):
        pass
    return isinstance(x, str) and x.strip() == ""


def _cohort_pct_text(cohort_pct: Any) -> str:
    try:
        return f"{float(cohort_pct) * 100:.0f}th percentile" if cohort_pct is not None else "top"
    except Exception:
        return "top"


def _fmt(x: Any) -> str:
    try:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return "NA"
        return f"{float(x):.3f}"
    except Exception:
        return str(x)


def _fmt_num(x: Any, default: Any = 0.0) -> Any:
    """Parse to float; return ``default`` (0.0 by default, or None) on None/NaN/parse failure."""
    if x is None:
        return default
    try:
        if isinstance(x, float) and pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default
