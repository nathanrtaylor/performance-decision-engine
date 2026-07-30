from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from common import (
    RunPaths,
    apply_agent_metrics_from_catalog,
    load_yaml,
    repo_root_from_this_file,
    sha256_text,
    utc_now_iso,
    validate_min_config,
    write_json,
)
from compile_sql import compile_all
from presto_runner import load_presto_conn_from_env, run_query_to_df


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)

def update_latest_pointer(run_outputs_dir: Path) -> None:
    """
    Replace configs/data/raw/weekly/latest with a copy of the current run folder.
    """
    weekly_root = run_outputs_dir.parent
    latest_dir = weekly_root / "latest"

    # Remove existing latest if present
    if latest_dir.exists():
        shutil.rmtree(latest_dir)

    # Copy current run folder to latest
    shutil.copytree(run_outputs_dir, latest_dir)

def run_extract(cfg_path: Path) -> Dict[str, Any]:
    cfg = load_yaml(cfg_path)
    apply_agent_metrics_from_catalog(cfg, repo_root_from_this_file())
    validate_min_config(cfg)

    # 1) compile (also writes compile manifest)
    compile_manifest = compile_all(cfg_path)

    paths = RunPaths.from_config(cfg)
    conn = load_presto_conn_from_env()

    run_manifest: Dict[str, Any] = {
        "run_id": cfg["run"]["run_id"],
        "started_at_utc": utc_now_iso(),
        "config_path": str(cfg_path),
        "compiled_dir": str(paths.compiled_dir),
        "outputs_dir": str(paths.outputs_dir),
        "globals": cfg.get("globals", {}),
        "items": [],
        "status": "running",
    }

    # 2) execute each compiled SQL, write output CSV
    for out_name, spec in cfg["outputs"].items():
        compiled_sql_path = paths.compiled_dir / f"{out_name}.sql"
        sql = compiled_sql_path.read_text(encoding="utf-8")
        sql_hash = sha256_text(sql)

        output_csv = paths.outputs_dir / spec["output_file"]

        item = {
            "output": out_name,
            "sql_file": spec["sql_file"],
            "compiled_sql_path": str(compiled_sql_path),
            "output_file": spec["output_file"],
            "output_path": str(output_csv),
            "sql_sha256": sql_hash,
            "started_at_utc": utc_now_iso(),
            "status": "running",
        }

        try:
            df, meta = run_query_to_df(sql, conn)
            write_csv(df, output_csv)

            item.update(
                {
                    "status": "success",
                    "finished_at_utc": utc_now_iso(),
                    "row_count": meta["row_count"],
                    "columns": meta["columns"],
                    "runtime_seconds": meta["runtime_seconds"],
                    "query_id": meta.get("query_id"),
                }
            )
        except Exception as e:
            item.update(
                {
                    "status": "failed",
                    "finished_at_utc": utc_now_iso(),
                    "error": repr(e),
                }
            )
            run_manifest["items"].append(item)
            run_manifest["status"] = "failed"
            write_json(paths.outputs_dir / "manifest.json", run_manifest)
            raise

        run_manifest["items"].append(item)

    run_manifest["finished_at_utc"] = utc_now_iso()
    run_manifest["status"] = "success"

    write_json(paths.outputs_dir / "manifest.json", run_manifest)

    # Maintain latest pointer
    update_latest_pointer(paths.outputs_dir)

    return run_manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="Path to extraction YAML run spec")
    args = ap.parse_args()

    manifest = run_extract(Path(args.config))
    print(f"Extract complete: {manifest['status']} -> {manifest['outputs_dir']}")

if __name__ == "__main__":
    main()