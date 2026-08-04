# src/cde/governance/versioning.py

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

from cde.utils.config import unwrap_root as _unwrap_root
from cde.utils.io import load_yaml

# Keys under cfg["meta"] that vary per run (not config content) and must be excluded
# from the content hash so the same governed config set always hashes the same.
_VOLATILE_META_KEYS = {"data_snapshot", "config_hash"}


def config_content_hash(config: Dict[str, Any]) -> str:
    """Deterministic short hash of the resolved config's *content*.

    Excludes volatile per-run provenance (``meta.data_snapshot`` and any prior
    ``config_hash``) so two runs on the same governed configs hash identically,
    while changing any config value changes the hash -- provenance that tracks
    content even when a hand-edited ``meta.version`` isn't bumped.
    """
    meta = {k: v for k, v in (config.get("meta") or {}).items() if k not in _VOLATILE_META_KEYS}
    hashable = {**config, "meta": meta}
    payload = json.dumps(hashable, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def resolve_raw_export_dir(configs_dir: Path, config: Dict[str, Any]) -> Path:
    """
    Resolve raw snapshot directory from config['data_snapshot'].
    Relative ``data_snapshot.root`` paths are resolved against the repo root (parent of ``configs_dir``).
    """
    repo_root = configs_dir.resolve().parent
    ds = config.get("data_snapshot") or {}
    if not ds:
        raise ValueError(
            "No raw directory configured: pass --raw-dir or define data_snapshot in configs/active.yaml."
        )
    mode = str(ds.get("mode", "latest")).lower()
    root = Path(ds.get("root", "data/raw/weekly"))
    if not root.is_absolute():
        root = (repo_root / root).resolve()
    if mode == "latest":
        return root / "latest"
    if mode == "explicit":
        sid = ds.get("snapshot_id")
        if not sid:
            raise ValueError("data_snapshot.mode is 'explicit' but snapshot_id is missing.")
        return (root / str(sid)).resolve()
    raise ValueError(f"Unknown data_snapshot.mode: {mode!r} (expected 'latest' or 'explicit').")


def resolve_active_config(configs_dir: Path) -> Dict[str, Any]:
    """
    Loads configs/active.yaml and all referenced config files into a single runtime config.

    Expected active.yaml (example):

      mappings:
        source_catalog: mappings/source_catalog.yaml
        metric_catalog: mappings/metric_catalog.yaml
        topic_map: mappings/topic_map.yaml
        benchmarks: mappings/benchmarks.yaml

      thresholds: thresholds/signal_thresholds.yaml
      priorities: priorities/v2026_02_16_ops_hotfix.yaml

      # Optional knobs can live directly in active.yaml too:
      entity_keys:
        agent_id: agent_id
        period: week_start

      call_type_column: call_type
      signal_window:
        volatility_periods: 4

      meta:
        version: v2026_02_16_ops_hotfix
        data_snapshot: 2026-02-16_weekly
        engine_version: 0.1.0
    """
    active_path = configs_dir / "active.yaml"
    active = load_yaml(active_path)

    cfg: Dict[str, Any] = {}

    # ---- meta / provenance
    cfg["meta"] = active.get("meta", {}) or {}

    # ---- thresholds (raw as loaded; downstream uses thresholds["signal_thresholds"])
    if "thresholds" in active and active["thresholds"]:
        cfg["thresholds"] = load_yaml(configs_dir / active["thresholds"])

    # ---- priorities (store under cfg["priorities"], unwrapped if needed)
    if "priorities" in active and active["priorities"]:
        pri = load_yaml(configs_dir / active["priorities"])
        cfg["priorities"] = _unwrap_root(pri, "priorities")

    # ---- mappings
    # Keep wrapper for schema-preserving configs (catalogs, topic_map),
    # but unwrap benchmarks into cfg["benchmarks"] because benchmark lookup expects it.
    mappings = active.get("mappings") or {}
    for name, rel_path in mappings.items():
        loaded = load_yaml(configs_dir / rel_path)

        if name == "benchmarks":
            cfg["benchmarks"] = _unwrap_root(loaded, "benchmarks")
        else:
            cfg[name] = loaded

    # ---- optional knobs (pass-through)
    passthrough_keys = [
        "entity_keys",
        "required_tables",
        "priority_model",
        "risk_model",
        "eligibility",
        "dampening",
        "tie_breakers",
        "conversation_types",
        "call_type_column",
        "call_type_mode",
        "default_call_type",
        "signal_window",
        "explainability",
        "normalization",
        "topic_map_options",
        "data_snapshot",
        "theme_selection",
        "break_glass",
        "abstention",
        "benchmark_recalc",
        "theme_discovery",
    ]
    for k in passthrough_keys:
        if k in active:
            cfg[k] = active[k]

    if "required_tables" not in cfg:
        ds = cfg.get("data_snapshot") or {}
        exp = ds.get("expected_sources") or []
        if exp:
            cfg["required_tables"] = list(exp)

    # ---- safe defaults
    cfg.setdefault("entity_keys", {"agent_id": "agent_id", "period": "week_start"})
    cfg.setdefault("call_type_column", "call_type")
    cfg.setdefault("signal_window", {"volatility_periods": 4})
    cfg.setdefault("priority_model", {"w_level": 0.5, "w_trend": 0.2, "w_risk": 0.3, "w_confidence": 0.0})
    cfg.setdefault("risk_model", {"alpha_level": 0.7, "beta_trend": 0.3})

    # ---- meta defaults (helpful for receipts/manifests)
    meta = cfg.get("meta") or {}
    # If meta.version not set, infer from priorities file name if present
    if "version" not in meta and "priorities" in active and active["priorities"]:
        meta["version"] = Path(active["priorities"]).stem
    meta.setdefault("engine_version", "0.1.0")
    cfg["meta"] = meta

    return cfg
