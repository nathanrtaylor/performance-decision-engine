from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from pde.utils.io import read_parquet_or_csv


@dataclass(frozen=True)
class RawInputs:
    """Container for raw, externally-produced exports."""
    tables: Dict[str, pd.DataFrame]
    source_dir: Path


def load_raw_exports(raw_dir: Path) -> RawInputs:
    """
    Loads all csv/parquet files in a folder into a dict keyed by stem name.

    Example:
      data/raw/weekly/2026-02-16/agent_metrics.csv -> tables["agent_metrics"]
    """
    if not raw_dir.exists():
        raise FileNotFoundError(f"Raw dir does not exist: {raw_dir}")

    tables: Dict[str, pd.DataFrame] = {}
    for p in sorted(raw_dir.glob("*")):
        if p.is_dir():
            continue
        if p.suffix.lower() not in {".csv", ".parquet"}:
            continue
        tables[p.stem] = read_parquet_or_csv(p)

    if not tables:
        raise ValueError(f"No csv/parquet files found in raw_dir: {raw_dir}")

    return RawInputs(tables=tables, source_dir=raw_dir)
