# src/cde/simulation/exports.py

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from cde.governance.audit import RunAuditor
from cde.utils.io import ensure_dir
from cde.engine.receipts import receipts_to_jsonl


def export_run_artifacts(
    out_dir: Path,
    auditor: RunAuditor,
    recommendations: pd.DataFrame,
    receipts: pd.DataFrame,
    config: Dict[str, Any],
    excluded_signals: Optional[pd.DataFrame] = None,
    abstentions: Optional[pd.DataFrame] = None,
) -> None:
    """
    Write all run artifacts to outputs/runs/<run_id>/...

    Always writes:
      - config_snapshot/config_runtime.json
      - recommendations.csv (actionable recommendations only)
      - decision_receipts.jsonl (recommendations + abstentions)

    Optionally writes:
      - excluded_signals.csv (signal-level gating failures with reason codes)
      - abstentions.csv (agents with no recommendation + reason)
    """
    ensure_dir(out_dir)

    # Freeze config used in this run (critical for auditability)
    auditor.snapshot_config(config)

    # Recommendations
    rec_path = out_dir / "recommendations.csv"
    recommendations.to_csv(rec_path, index=False)

    # Receipts (JSONL)
    receipts_path = out_dir / "decision_receipts.jsonl"
    receipts_path.write_text(receipts_to_jsonl(receipts), encoding="utf-8")

    # Excluded signals (audit/debug)
    if excluded_signals is not None and not excluded_signals.empty:
        excluded_path = out_dir / "excluded_signals.csv"
        excluded_signals.to_csv(excluded_path, index=False)

    # Abstentions (agents with an explicit non-recommendation + reason)
    if abstentions is not None and not abstentions.empty:
        abstentions.to_csv(out_dir / "abstentions.csv", index=False)
