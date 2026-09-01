"""CLI: interpret raw TrAIning Assist behavior rows into skill-level pass-rates.

Reads the raw behavior-grain extract, applies the behavior->skill mapping and
per-profile relevance rules from configs/mappings/training_profiles.yaml, and writes the
skill-level tall-skinny CSV that build_signals consumes.

    # default: transform latest snapshot in place
    python -m cde.cli.build_training_assist_skills

    # explicit paths
    python -m cde.cli.build_training_assist_skills \
        --raw-dir data/raw/weekly/latest \
        --profiles configs/mappings/training_profiles.yaml

The output (`training_assist_skills.csv`) is written into --raw-dir so a subsequent
run_pipeline over that same dir picks it up automatically (load_raw_exports keys by
filename stem). The raw `training_assist.csv` stays as the transform's input only.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cde.ingestion.training_assist_skills import build_training_assist_skills
from cde.utils.io import load_yaml, read_parquet_or_csv
from cde.utils.logging import get_logger

log = get_logger(__name__)

_DEFAULT_RAW_DIR = "data/raw/weekly/latest"
_DEFAULT_PROFILES = "configs/mappings/training_profiles.yaml"
_INPUT_NAME = "training_assist.csv"
_OUTPUT_NAME = "training_assist_skills.csv"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Reframe raw TrAIning Assist behaviors into skill-level pass-rates."
    )
    ap.add_argument("--raw-dir", default=_DEFAULT_RAW_DIR,
                    help=f"Snapshot dir containing {_INPUT_NAME} (default: {_DEFAULT_RAW_DIR})")
    ap.add_argument("--profiles", default=_DEFAULT_PROFILES,
                    help=f"Path to training_profiles.yaml (default: {_DEFAULT_PROFILES})")
    ap.add_argument("--out", default=None,
                    help=f"Output CSV path (default: <raw-dir>/{_OUTPUT_NAME})")
    args = ap.parse_args()

    raw_dir = Path(args.raw_dir)
    in_path = raw_dir / _INPUT_NAME
    out_path = Path(args.out) if args.out else raw_dir / _OUTPUT_NAME

    if not in_path.exists():
        log.error("Input not found: %s (run the extract first, or point --raw-dir at it)", in_path)
        return 1

    raw_df = read_parquet_or_csv(in_path)
    profiles = load_yaml(args.profiles)

    skills_df = build_training_assist_skills(raw_df, profiles)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    skills_df.to_csv(out_path, index=False)

    n_skills = skills_df["skill"].nunique() if not skills_df.empty else 0
    log.info(
        "Wrote %s: %d rows across %d skills (from %d raw behavior rows).",
        out_path, len(skills_df), n_skills, len(raw_df),
    )
    print(f"training_assist_skills -> {out_path} ({len(skills_df)} rows, {n_skills} skills)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
