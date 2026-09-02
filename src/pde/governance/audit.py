from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from pde.utils.io import ensure_dir, dump_json


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class RunAuditor:
    out_dir: Path
    run_id: Optional[str] = None
    manifest: Dict[str, Any] = field(default_factory=dict)

    def start_run(self) -> None:
        ensure_dir(self.out_dir)
        if self.run_id is None:
            self.run_id = datetime.utcnow().strftime("%Y-%m-%d_%H%M%S")
        self.manifest["run_id"] = self.run_id
        self.manifest["started_utc"] = datetime.utcnow().isoformat()

    def record_inputs(self, raw_dir: Optional[Path], config: Dict[str, Any], extra_inputs: Optional[Dict[str, Any]] = None) -> None:
        self.manifest["inputs"] = {}
        if raw_dir:
            self.manifest["inputs"]["raw_dir"] = str(raw_dir)

            hashes = {}
            for p in sorted(raw_dir.glob("*")):
                if p.is_file() and p.suffix.lower() in {".csv", ".parquet"}:
                    hashes[p.name] = _sha256_file(p)
            self.manifest["inputs"]["raw_hashes"] = hashes

        self.manifest["config_meta"] = config.get("meta", {})
        if extra_inputs:
            self.manifest["inputs"].update(extra_inputs)

    def snapshot_config(self, config: Dict[str, Any]) -> None:
        snap_dir = self.out_dir / "config_snapshot"
        ensure_dir(snap_dir)
        dump_json(snap_dir / "config_runtime.json", config)

    def finish_run(self) -> None:
        self.manifest["finished_utc"] = datetime.utcnow().isoformat()
        dump_json(self.out_dir / "manifest.json", self.manifest)
