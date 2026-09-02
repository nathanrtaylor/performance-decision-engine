from __future__ import annotations

from pathlib import Path
from typing import Optional


def repo_root() -> Path:
    # src/pde/io/paths.py -> io -> pde -> src -> repo root
    return Path(__file__).resolve().parents[3]


def get_raw_weekly_path(run_id: Optional[str] = None) -> Path:
    """
    Raw weekly extracts live at:
      configs/data/raw/weekly/<run_id>/
    And the engine consumption surface is:
      configs/data/raw/weekly/latest/
    """
    base = repo_root() / "configs" / "data" / "raw" / "weekly"
    return base / (run_id if run_id else "latest")