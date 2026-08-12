from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from cde.explainability.templates import _fmt_num, _percentile_band


def build_competitors(recommendations: pd.DataFrame, candidates: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """
    For each recommendation, return the top competing topics with a data-grounded reason
    for why each was not selected.
    """
    top_k = int((config.get("explainability") or {}).get("top_competitors", 3))
    cand = candidates.copy()

    key_cols = ["agent_id", "period", "call_type"]
    out_rows = []

    for _, rec in recommendations.iterrows():
        key = {k: rec.get(k) for k in key_cols}
        chosen = rec["topic"]

        subset = cand
        for k, v in key.items():
            subset = subset[subset[k] == v]

        subset = subset[subset["topic"] != chosen].copy()
        subset = subset.sort_values(["priority_score", "risk_score", "confidence_score", "topic"], ascending=[False, False, False, True]).head(top_k)

        for _, row in subset.iterrows():
            out_rows.append(
                {
                    "agent_id": key["agent_id"],
                    "period": key["period"],
                    "call_type": key["call_type"],
                    "topic": row["topic"],
                    "metric": row.get("metric"),
                    "gap": _fmt_num(row.get("gap"), default=None),
                    "level_score": float(row.get("level_score", 0.0)),
                    "priority_score": float(row.get("priority_score", 0.0)),
                    "risk_score": float(row.get("risk_score", 0.0)),
                    "confidence_score": float(row.get("confidence_score", 0.0)),
                    "reason_not_selected": _reason_not_selected(row, rec),
                }
            )

    return pd.DataFrame(out_rows)


def _reason_not_selected(candidate_row: pd.Series, chosen_row: pd.Series) -> str:
    """
    Explain, in plain language grounded in the peer-standing deficit, why the chosen topic
    beat this candidate. Avoids exposing internal scores.
    """
    c_lvl = _fmt_num(candidate_row.get("level_score"))
    w_lvl = _fmt_num(chosen_row.get("level_score"))
    if w_lvl - c_lvl > 0.05:
        return (
            "the chosen topic sits further below its benchmark relative to peers "
            f"({_percentile_band(w_lvl)} vs {_percentile_band(c_lvl)})"
        )
    return "it was an even call and the chosen topic edged ahead on the combined evidence"
