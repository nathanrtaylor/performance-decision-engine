from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml


def load_yaml(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"YAML not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_text(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, obj: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def repo_root_from_this_file() -> Path:
    # extraction/scripts/common.py -> extraction/scripts -> extraction -> repo root
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RunPaths:
    repo_root: Path
    compiled_dir: Path
    outputs_dir: Path

    @staticmethod
    def from_config(cfg: Dict[str, Any]) -> "RunPaths":
        root = repo_root_from_this_file()
        run_id = cfg["run"]["run_id"]

        compiled_base = cfg["run"].get("compiled_sql_dir", "extraction/compiled_sql")
        outputs_base = cfg["run"].get("output_dir", "extraction/outputs")

        compiled_dir = root / compiled_base / run_id
        outputs_dir = root / outputs_base / run_id
        return RunPaths(repo_root=root, compiled_dir=compiled_dir, outputs_dir=outputs_dir)


def apply_agent_metrics_from_catalog(cfg: Dict[str, Any], repo_root: Path) -> None:
    """
    If outputs.agent_metrics.params.metrics_from_catalog is set, load metric_catalog
    and fill params.metrics with source_metric_key for every metric with source==agent_metrics.
    Removes metrics_from_catalog from params after merging (Jinja uses metrics only).
    """
    out = (cfg.get("outputs") or {}).get("agent_metrics")
    if not out:
        return
    params = out.setdefault("params", {})
    rel = params.get("metrics_from_catalog")
    if not rel:
        return
    cat_path = (repo_root / rel).resolve()
    if not cat_path.exists():
        raise FileNotFoundError(f"metrics_from_catalog not found: {cat_path}")
    raw = load_yaml(cat_path)
    mc = raw.get("metric_catalog") if isinstance(raw, dict) else {}
    metrics_block = (mc.get("metrics") if isinstance(mc, dict) else None) or {}
    keys: list[str] = []
    for _name, spec in metrics_block.items():
        if not isinstance(spec, dict):
            continue
        if spec.get("source") != "agent_metrics":
            continue
        k = spec.get("source_metric_key")
        if k:
            keys.append(str(k))
    if not keys:
        raise ValueError("metrics_from_catalog produced no agent_metrics source_metric_key entries")
    params["metrics"] = keys
    params.pop("metrics_from_catalog", None)


def deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Return merged dict: values from b override/extend a."""
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def validate_min_config(cfg: Dict[str, Any]) -> None:
    for key in ["run", "globals", "outputs"]:
        if key not in cfg:
            raise ValueError(f"Config missing required top-level key: '{key}'")
    if "run_id" not in cfg["run"]:
        raise ValueError("Config missing required key: run.run_id")
    if not isinstance(cfg["outputs"], dict) or not cfg["outputs"]:
        raise ValueError("Config outputs must be a non-empty mapping")
    # enforce uniqueness of output_file names
    seen = set()
    for name, spec in cfg["outputs"].items():
        if "sql_file" not in spec:
            raise ValueError(f"Output '{name}' missing sql_file")
        if "output_file" not in spec:
            raise ValueError(f"Output '{name}' missing output_file")
        of = spec["output_file"]
        if of in seen:
            raise ValueError(f"Duplicate output_file '{of}' in outputs")
        seen.add(of)