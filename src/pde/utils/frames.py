"""Small dataframe helpers shared across ingestion, prioritization, and scoring.

One home per helper (see the simplification refactor): ``require_cols`` was copy-pasted
in ``ingestion/validate.py`` and ``prioritization/apply.py`` with drifting error text.
This is the canonical version — it raises the more diagnostic message (missing columns
plus the columns actually present). It never fires on valid input, so consolidating it
does not change any successful run.
"""
from __future__ import annotations

from typing import Iterable

import pandas as pd


def require_cols(df: pd.DataFrame, cols: Iterable[str], name: str) -> None:
    """Raise ``ValueError`` if ``df`` is missing any of ``cols``.

    ``name`` labels the table in the error. On valid input this is a no-op.
    """
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(
            f"{name} missing required columns {missing}. cols={df.columns.tolist()}"
        )
