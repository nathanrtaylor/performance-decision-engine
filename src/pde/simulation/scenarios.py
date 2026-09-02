from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from pde.utils.io import load_yaml


def load_scenarios(path: Path) -> List[Dict[str, Any]]:
    """
    Loads a list of scenarios.

    YAML example:
      - name: "Increase transfer focus"
        changes:
          priorities:
            weights:
              global:
                transfer_rate: 2.0
    """
    if path.suffix.lower() in {".yaml", ".yml"}:
        data = load_yaml(path)
    elif path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        raise ValueError("Scenario file must be .yaml/.yml or .json")

    if not isinstance(data, list):
        raise ValueError("Scenarios file must contain a list of scenarios.")
    return data
