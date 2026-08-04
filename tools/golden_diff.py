#!/usr/bin/env python
"""Golden-diff gate for pure-refactor work.

Compares the *decision output* of two pipeline runs -- recommendations.csv,
abstentions.csv, and decision_receipts.jsonl -- and exits non-zero if they
differ in any way that matters. Volatile provenance fields (timestamps, run ids,
config/data hashes) are stripped before comparison so a pure refactor that only
changes when/how a run was produced still compares equal.

Usage:
    python tools/golden_diff.py <baseline_dir> <candidate_dir> [--ignore-keys k1,k2]

Intended workflow:
    1. Capture a baseline run once (before any code change).
    2. After each refactor phase, re-run the pipeline into a fresh dir.
    3. golden_diff baseline candidate  ->  exit 0 == output preserved.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

# Provenance / run-metadata fields that legitimately differ run-to-run and must
# not fail a pure-refactor comparison.
DEFAULT_IGNORE = {
    "run_id",
    "generated_at",
    "created_at",
    "timestamp",
    "run_timestamp",
    "config_hash",       # added in Phase C; volatile by design
    "engine_run_id",
    "out_dir",
}

# Row identity for the tabular artifacts.
KEY_COLS = ["agent_id", "period", "call_type"]


def _fail(msg: str) -> None:
    print(f"GOLDEN-DIFF FAIL: {msg}")


def _compare_csv(name: str, a_dir: Path, b_dir: Path) -> bool:
    a_path, b_path = a_dir / name, b_dir / name
    if a_path.exists() != b_path.exists():
        _fail(f"{name}: present in only one run (baseline={a_path.exists()}, candidate={b_path.exists()})")
        return False
    if not a_path.exists():
        return True  # absent in both -> fine

    a = pd.read_csv(a_path)
    b = pd.read_csv(b_path)

    if set(a.columns) != set(b.columns):
        _fail(f"{name}: column set differs\n  only in baseline: {set(a.columns) - set(b.columns)}"
              f"\n  only in candidate: {set(b.columns) - set(a.columns)}")
        return False

    b = b[a.columns]  # align column order
    keys = [k for k in KEY_COLS if k in a.columns]
    sort_cols = keys if keys else list(a.columns)
    a = a.sort_values(sort_cols).reset_index(drop=True)
    b = b.sort_values(sort_cols).reset_index(drop=True)

    if len(a) != len(b):
        _fail(f"{name}: row count differs (baseline={len(a)}, candidate={len(b)})")
        return False

    # Compare with NaN==NaN and float tolerance.
    try:
        pd.testing.assert_frame_equal(a, b, check_like=False, check_dtype=False, rtol=1e-9, atol=1e-12)
    except AssertionError as e:
        _fail(f"{name}: cell values differ\n{e}")
        return False

    print(f"  OK  {name}: {len(a)} rows identical")
    return True


def _load_receipts(path: Path, ignore: set[str]) -> dict[tuple, dict]:
    out: dict[tuple, dict] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            key = tuple(str(rec.get(k, "")) for k in KEY_COLS)
            out[key] = {k: v for k, v in rec.items() if k not in ignore}
    return out


def _canon(v):
    # Stable, order-independent JSON for nested structures.
    return json.dumps(v, sort_keys=True, default=str)


def _compare_receipts(name: str, a_dir: Path, b_dir: Path, ignore: set[str]) -> bool:
    a_path, b_path = a_dir / name, b_dir / name
    if a_path.exists() != b_path.exists():
        _fail(f"{name}: present in only one run")
        return False
    if not a_path.exists():
        return True

    a = _load_receipts(a_path, ignore)
    b = _load_receipts(b_path, ignore)

    if a.keys() != b.keys():
        only_a = list(a.keys() - b.keys())[:5]
        only_b = list(b.keys() - a.keys())[:5]
        _fail(f"{name}: receipt key set differs "
              f"(baseline={len(a)}, candidate={len(b)}); e.g. only-baseline={only_a}, only-candidate={only_b}")
        return False

    diffs = 0
    for key in a:
        if _canon(a[key]) != _canon(b[key]):
            diffs += 1
            if diffs <= 5:
                fields = sorted(set(a[key]) | set(b[key]))
                changed = [f for f in fields if _canon(a[key].get(f)) != _canon(b[key].get(f))]
                _fail(f"{name} {key}: fields differ: {changed}")
    if diffs:
        _fail(f"{name}: {diffs} receipts differ (ignoring {sorted(ignore)})")
        return False

    print(f"  OK  {name}: {len(a)} receipts identical")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Golden-diff gate for pure-refactor work")
    ap.add_argument("baseline")
    ap.add_argument("candidate")
    ap.add_argument("--ignore-keys", default="", help="extra comma-separated receipt keys to ignore")
    args = ap.parse_args()

    a_dir, b_dir = Path(args.baseline), Path(args.candidate)
    ignore = set(DEFAULT_IGNORE)
    if args.ignore_keys:
        ignore |= {k.strip() for k in args.ignore_keys.split(",") if k.strip()}

    print(f"Comparing:\n  baseline : {a_dir}\n  candidate: {b_dir}")
    ok = True
    ok &= _compare_csv("recommendations.csv", a_dir, b_dir)
    ok &= _compare_csv("abstentions.csv", a_dir, b_dir)
    ok &= _compare_receipts("decision_receipts.jsonl", a_dir, b_dir, ignore)

    if ok:
        print("GOLDEN-DIFF PASS: decision output is identical.")
        return 0
    print("GOLDEN-DIFF FAIL: see differences above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
