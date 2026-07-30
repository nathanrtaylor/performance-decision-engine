# extraction/scripts/compile_sql.py
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any, Dict

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from common import (
    RunPaths,
    apply_agent_metrics_from_catalog,
    deep_merge,
    load_yaml,
    repo_root_from_this_file,
    sha256_text,
    utc_now_iso,
    validate_min_config,
    write_json,
    write_text,
)


def build_context(cfg: Dict[str, Any], output_name: str) -> Dict[str, Any]:
    """Merge globals + per-output params + derived fields into a single template context."""
    globals_ctx = cfg.get("globals", {}) or {}
    output_spec = cfg["outputs"][output_name]
    overrides = output_spec.get("params", {}) or {}

    derived = {
        "run_id": cfg["run"]["run_id"],
        "extraction_ts_utc": utc_now_iso(),
    }
    # Merge order: globals -> overrides -> derived
    return deep_merge(deep_merge(globals_ctx, overrides), derived)


def flatten_sql(sql: str, strip_trailing_semicolon: bool = True) -> str:
    """
    Flatten SQL for Presto gateways that dislike formatting.
    - removes -- and /* */ comments
    - collapses whitespace/newlines into single spaces
    - optionally strips a trailing semicolon
    """
    s = sql

    # Remove block comments /* ... */
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)

    # Remove line comments -- ...
    s = re.sub(r"--[^\n]*", " ", s)

    # Collapse all whitespace into single spaces
    s = re.sub(r"\s+", " ", s).strip()

    if strip_trailing_semicolon:
        s = s[:-1].strip() if s.endswith(";") else s

    return s + "\n"


def guard_single_statement(sql: str, out_name: str) -> None:
    """
    Prevent accidental multi-statement SQL.
    After optional stripping of a trailing semicolon, any remaining semicolon is suspicious.
    """
    # Ignore semicolons inside single quotes (very conservative quick pass)
    # This is not a full SQL parser; it is just a guardrail.
    in_quote = False
    for ch in sql:
        if ch == "'":
            in_quote = not in_quote
        if ch == ";" and not in_quote:
            raise ValueError(
                f"Compiled SQL for '{out_name}' contains a semicolon; multi-statement SQL is not allowed."
            )


def compile_all(cfg_path: Path) -> Dict[str, Any]:
    cfg = load_yaml(cfg_path)
    apply_agent_metrics_from_catalog(cfg, repo_root_from_this_file())
    validate_min_config(cfg)

    paths = RunPaths.from_config(cfg)

    # Run-level behavior toggles (governed in YAML)
    flatten = bool(cfg["run"].get("flatten_sql", True))
    strip_sc = bool(cfg["run"].get("strip_trailing_semicolon", True))

    # Jinja environment rooted at repo root so sql_file paths resolve as provided
    env = Environment(
        loader=FileSystemLoader(str(paths.repo_root)),
        undefined=StrictUndefined,  # fail fast if params are missing
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )

    compiled_items = []
    for out_name, spec in cfg["outputs"].items():
        sql_file = spec["sql_file"]
        template = env.get_template(sql_file)
        context = build_context(cfg, out_name)

        rendered = template.render(**context).strip()

        # Ensure no unresolved template tokens remain
        if "{{" in rendered or "{%" in rendered:
            raise ValueError(f"Unresolved template tokens remain in compiled SQL for '{out_name}'")

        if flatten:
            rendered = flatten_sql(rendered, strip_trailing_semicolon=strip_sc)
        else:
            # still normalize to newline-terminated file
            if strip_sc and rendered.endswith(";"):
                rendered = rendered[:-1].rstrip()
            rendered = rendered + "\n"

        # Multi-statement guardrail
        guard_single_statement(rendered, out_name)

        compiled_path = paths.compiled_dir / f"{out_name}.sql"
        write_text(compiled_path, rendered)

        compiled_items.append(
            {
                "output": out_name,
                "sql_file": sql_file,
                "compiled_sql_path": str(compiled_path),
                "sql_sha256": sha256_text(rendered),
                "flattened": flatten,
                "strip_trailing_semicolon": strip_sc,
                "context_keys": sorted(list(context.keys())),
            }
        )

    manifest = {
        "run_id": cfg["run"]["run_id"],
        "compiled_at_utc": utc_now_iso(),
        "compiled_dir": str(paths.compiled_dir),
        "items": compiled_items,
    }
    write_json(paths.compiled_dir / "_compile_manifest.json", manifest)
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="Path to extraction YAML run spec")
    args = ap.parse_args()

    manifest = compile_all(Path(args.config))
    print(f"Compiled {len(manifest['items'])} queries into {manifest['compiled_dir']}")


if __name__ == "__main__":
    main()