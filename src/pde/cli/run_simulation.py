from __future__ import annotations

import argparse
from pathlib import Path

from pde.governance.versioning import resolve_active_config
from pde.utils.io import read_parquet_or_csv, ensure_dir
from pde.simulation.scenarios import load_scenarios
from pde.simulation.preview import preview_scenarios


def main() -> None:
    parser = argparse.ArgumentParser(description="Run sensitivity analysis preview for priority scenarios.")
    parser.add_argument("--candidates", type=str, required=True, help="Path to candidates file (csv/parquet)")
    parser.add_argument("--configs-dir", type=str, default="configs")
    parser.add_argument("--scenarios", type=str, required=True, help="Path to scenarios yaml/json")
    parser.add_argument("--out-dir", type=str, required=True, help="Output folder for scenario preview artifacts")
    args = parser.parse_args()

    candidates = read_parquet_or_csv(Path(args.candidates))
    config = resolve_active_config(Path(args.configs_dir))
    scenarios = load_scenarios(Path(args.scenarios))

    out_dir = Path(args.out_dir)
    ensure_dir(out_dir)

    preview_df, details = preview_scenarios(candidates, config, scenarios)

    preview_path = out_dir / "scenario_preview.csv"
    preview_df.to_csv(preview_path, index=False)

    details_path = out_dir / "scenario_details.json"
    details_path.write_text(details, encoding="utf-8")

    print(f"Wrote: {preview_path}")
    print(f"Wrote: {details_path}")


if __name__ == "__main__":
    main()
