# src/cde/utils/io.py
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Dict, Union

import pandas as pd


def ensure_dir(path: Union[str, Path]) -> Path:
    """
    Ensure a directory exists. Returns the Path object.
    """
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_parquet_or_csv(path: Union[str, Path]) -> pd.DataFrame:
    """
    Read a parquet or csv file into a DataFrame.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))

    if p.suffix.lower() == ".parquet":
        return pd.read_parquet(p)
    if p.suffix.lower() == ".csv":
        return pd.read_csv(p)

    raise ValueError(f"Unsupported file type: {p.suffix} for {p}")


def load_yaml(path: Union[str, Path]) -> Dict[str, Any]:
    """
    Load YAML file into a dict.

    Encoding-tolerant: config files edited on Windows may carry a UTF-8 BOM or be
    saved as cp1252. Try utf-8, then utf-8-sig, then cp1252 before giving up.
    """
    import yaml

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    for enc in ("utf-8", "utf-8-sig"):
        try:
            return yaml.safe_load(p.read_text(encoding=enc))
        except UnicodeDecodeError:
            continue
    return yaml.safe_load(p.read_text(encoding="cp1252"))


def _json_default(x: Any) -> Any:
    """
    Deterministic JSON serializer for common non-JSON-native types.
    """
    if isinstance(x, (_dt.datetime, _dt.date)):
        return x.isoformat()

    if isinstance(x, Path):
        return str(x)

    try:
        import numpy as np
        if isinstance(x, (np.integer, np.floating, np.bool_)):
            return x.item()
    except Exception:
        pass

    try:
        import pandas as _pd
        if isinstance(x, (_pd.Timestamp, _pd.Timedelta)):
            return str(x)
    except Exception:
        pass

    raise TypeError(f"Object of type {x.__class__.__name__} is not JSON serializable")


def dump_json(path: Union[str, Path], obj: Any) -> None:
    """
    Write object as JSON to a file with safe serialization.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
