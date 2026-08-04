"""Shared config-shape helpers.

Historically each module grew its own copy of the same "unwrap one YAML root
level" helper (7 of them, byte-identical in behavior) because configs are loaded
in two shapes: ``{root_key: {...}}`` (recommended) or an already-unwrapped
``{...}``. This is the single canonical implementation.
"""
from __future__ import annotations

from typing import Any, Dict


def unwrap_root(obj: Any, root_key: str) -> Dict[str, Any]:
    """Return the inner mapping for ``root_key``.

    Accepts a block that may be wrapped (``{root_key: {...}}``) or already
    unwrapped (``{...}``). Non-dict input yields ``{}``.

        unwrap_root({"metric_catalog": {"metrics": {...}}}, "metric_catalog")
            -> {"metrics": {...}}
        unwrap_root({"metrics": {...}}, "metric_catalog")   # already unwrapped
            -> {"metrics": {...}}
        unwrap_root(None, "metric_catalog") -> {}
    """
    if isinstance(obj, dict) and isinstance(obj.get(root_key), dict):
        return obj[root_key]
    return obj if isinstance(obj, dict) else {}
