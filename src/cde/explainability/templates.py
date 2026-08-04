from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd


def narrative_why_this(rec_row: pd.Series) -> str:
    metric = rec_row.get("metric")
    gap = rec_row.get("gap")
    lvl = rec_row.get("level_score")
    return f"Selected because '{metric}' is the strongest driver for the chosen topic, with level_score={_fmt(lvl)} and gap={_fmt(gap)}."

def narrative_why_now(rec_row: pd.Series) -> str:
    trend = rec_row.get("trend_score")
    risk = rec_row.get("risk_score")
    conf = rec_row.get("confidence_score")
    return f"Recommended now due to urgency signals: trend_score={_fmt(trend)}, risk_score={_fmt(risk)}, confidence={_fmt(conf)}."

def narrative_why_not(competitors: List[Dict[str, Any]]) -> str:
    if not competitors:
        return "No other eligible topics exceeded thresholds under current rules."
    top = competitors[0]
    return f"Next-best alternative was '{top.get('topic')}', but it was not selected because: {top.get('reason_not_selected')}."


def narrative_theme_why_this(theme: str, drivers: List[Dict[str, Any]], n_deficient: int, n_members: int) -> str:
    metrics = ", ".join(str(d.get("metric")) for d in drivers) if drivers else "several behaviors"
    return (
        f"Coaching theme '{theme}' selected: {n_deficient} of {n_members} member behaviors are "
        f"deficient together ({metrics}), a pattern rather than a single issue."
    )


def narrative_theme_why_now(drivers: List[Dict[str, Any]]) -> str:
    if not drivers:
        return "Recommended now because multiple related behaviors are underperforming concurrently."
    worst = max(drivers, key=lambda d: float(d.get("risk_score") or 0.0))
    return (
        f"Recommended now: strongest member '{worst.get('metric')}' shows risk_score="
        f"{_fmt(worst.get('risk_score'))}, trend_score={_fmt(worst.get('trend_score'))}."
    )


def narrative_break_glass(row: Any) -> str:
    metric = row.get("metric")
    cohort_pct = row.get("cohort_pct")
    value = row.get("value")
    benchmark = row.get("benchmark")
    try:
        pct_txt = f"{float(cohort_pct) * 100:.0f}th percentile" if cohort_pct is not None else "top"
    except Exception:
        pct_txt = "top"
    return (
        f"Break-glass override: agent is in the worst cohort tail ({pct_txt}) of its ICP_Client "
        f"group for '{metric}' and below benchmark (value={_fmt(value)}, benchmark={_fmt(benchmark)}). "
        f"This critical single behavior takes precedence over any theme."
    )

def _fmt(x: Any) -> str:
    try:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return "NA"
        return f"{float(x):.3f}"
    except Exception:
        return str(x)
