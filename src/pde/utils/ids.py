from __future__ import annotations

import pandas as pd


def normalize_agent_id(s: pd.Series) -> pd.Series:
    """
    Canonicalize agent/expert ids to a stable string form so joins across sources match.

    Employee ids drift between an integer form ("12345") and zero-padded/float forms
    ("012345", "12345.0"). We collapse to the plain integer string when the value is numeric,
    otherwise fall back to a stripped string. Mirrors the int->varchar cast used in the SQL.
    """
    def _one(x: object) -> object:
        if pd.isna(x):
            return None
        xs = str(x).strip()
        if not xs:
            return None
        try:
            return str(int(float(xs)))
        except (ValueError, TypeError):
            return xs

    return s.map(_one)
