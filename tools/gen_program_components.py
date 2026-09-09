"""Enhance training_program.yaml with the Ascend Simulations curriculum.

Merges the ASCEND curriculum into the program:
  - SIMS: docs/training/"Ascend Simulations.xlsx" (sheet "Sims 100") -> per learning block,
    adds sim `components` (SIM ID + persona + topic + mode) and sets `gate.test_call` from the
    block's End-to-End Call sim.
  - CBTs: the curriculum course export (default docs/training/"Soluto_ASCND_VZW_Voice New
    Hire.csv", override with --curriculum-csv) -> its Modality == CBT rows become `kind: cbt`
    components. A CBT's `ref` is the live `cbt_<courseid>` metric key when its Workday
    /course/<guid> link carries a GUID, else a name slug (no courseid yet).

Existing block metadata (order, label, expected_completion_day, develops, gate.pass_mark,
...) is always preserved.

IMPORTANT -- crosswalk gap: the Ascend sim IDs/personas do NOT match the live TA
data's challenge_ids (0/93 overlap), so sim components are the CURRICULUM MAP, not yet
scoring-linked. A CBT `ref` of the form cbt_<courseid> IS the live metric key (scoring-linked);
a name-slug ref is curriculum-only until that course gets a courseid.

    python tools/gen_program_components.py                       # regenerate (writes the file)
    python tools/gen_program_components.py --dry-run             # preview only, no write
    python tools/gen_program_components.py --curriculum-csv PATH # CBTs from a different course export
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd
import yaml

from pde.training.program import cbt_metric_key

REPO = Path(__file__).resolve().parent.parent
PROGRAM = REPO / "configs/training/training_program.yaml"
SIMS = REPO / "docs/training/Ascend Simulations.xlsx"
# The CBT curriculum (block -> CBTs) is read straight from the training-strategy course export.
# A CBT's live Course ID is the GUID in its Workday /course/<guid> link (rows whose link has no
# GUID, e.g. .../d/inst/..., have no courseid and get a name-slug ref).
CURRICULUM_CSV = REPO / "docs/training/Soluto_ASCND_VZW_Voice New Hire.csv"
# Live CBT extract, used only to recover a courseid for CBTs whose Workday link has no /course/<guid>
# (many completion-only courses link to /d/inst/...). A course's id is recoverable here exactly when
# it has completion data that would need to nest, so the backfill is self-consistent.
CBT_EXTRACT = REPO / "data/raw/adhoc/latest/training_cbt.csv"

_E2E = "End-to-End Call"
_GATE_PRIORITY = ["readiness", "capstone", "gate", "end-to-end", "mixed queue"]
_COURSE_GUID = re.compile(r"/course/([0-9a-fA-F]{32})")


def _norm_name(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def _live_courseid_by_name(extract_csv: Path) -> dict:
    """normalized coursename -> courseid, from the live CBT extract (empty if absent)."""
    if not extract_csv.exists():
        return {}
    df = pd.read_csv(extract_csv, dtype={"courseid": str})
    if not {"courseid", "coursename"} <= set(df.columns):
        return {}
    out: dict = {}
    for _, r in df[["courseid", "coursename"]].dropna().drop_duplicates().iterrows():
        out.setdefault(_norm_name(r["coursename"]), r["courseid"])
    return out


def _snake(x) -> str | None:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    s = re.sub(r"[^0-9a-z]+", "_", str(x).strip().lower()).strip("_")
    return s or None


def _clean(x) -> str | None:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    return re.sub(r"\s+", " ", str(x).strip()) or None


def _sim_ref(x) -> str | None:
    """Cleaned SIM ID, with the WI-5 malformed 'ASC-ASC-SIM-' prefix (a spreadsheet typo) corrected."""
    r = _clean(x)
    return r.replace("ASC-ASC-SIM-", "ASC-SIM-") if r else r


def _norm_cbt_courseid(cid) -> str | None:
    """Live cbt_<courseid> metric key for a course id, or None if blank. (Key format lives in
    pde.training.program.cbt_metric_key -- the single source shared with the pipeline + catalog gen.)"""
    c = _clean(cid)
    return cbt_metric_key(c) if c else None


def _pick_gate(e2e: list[dict]) -> dict:
    """Primary gate among a block's End-to-End Call sims."""
    for kw in _GATE_PRIORITY:
        for s in e2e:
            if kw in str(s.get("Topic", "")).lower():
                return s
    return e2e[0]


# --------------------------------------------------------------------------- CBTs
def _cbt_components_by_block(curriculum_csv: Path, courseid_by_name: dict) -> dict[int, list[dict]]:
    """block order -> [cbt component dicts], parsed from the curriculum CSV (its ``Modality == CBT``
    rows). The courseid comes from the Workday ``/course/<guid>`` link, or (when the link has none)
    the live-extract coursename match; ``ref`` is then the ``cbt_<courseid>`` metric key -- which
    links to the scored metric and drives the dashboard's CBT->block nesting. Rows with no courseid
    from either source get a name-slug ref (curriculum-only). Returns {} if the file is absent."""
    if not curriculum_csv.exists():
        return {}
    df = pd.read_csv(curriculum_csv)
    out: dict[int, list[dict]] = {}
    for _, r in df.iterrows():
        if (_clean(r.get("Modality")) or "").lower() != "cbt":
            continue
        m = re.match(r"Learning Block\s*0*(\d+)", str(r.get("Learning Block") or ""))
        name = _clean(r.get("Course"))
        if not m or not name:
            continue
        gid = _COURSE_GUID.search(str(r.get("Workday Learning Link") or ""))
        courseid = gid.group(1) if gid else courseid_by_name.get(_norm_name(name))
        ref = _norm_cbt_courseid(courseid) if courseid else _snake(name)
        out.setdefault(int(m.group(1)), []).append(
            {"kind": "cbt", "ref": ref, "topic": name, "modality": "CBT", "develops": []})
    return out


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="Merge Ascend sims + CBTs into training_program.yaml.")
    ap.add_argument("--curriculum-csv", default=str(CURRICULUM_CSV),
                    help="Course export (block/course/modality/Workday link) that supplies the CBT<->block map.")
    ap.add_argument("--cbt-extract", default=str(CBT_EXTRACT),
                    help="Live training_cbt.csv, used only to recover courseids for CBTs whose Workday link lacks a GUID.")
    ap.add_argument("--dry-run", action="store_true", help="Print what would be written; do not modify the file.")
    args = ap.parse_args()

    prog = yaml.safe_load(PROGRAM.read_text(encoding="utf-8"))
    p = prog["program"]

    df = pd.read_excel(SIMS, sheet_name="Sims 100")
    df["_blk"] = pd.to_numeric(df["Learning Block"], errors="coerce")
    by_block: dict[int, list[dict]] = {}
    for _, r in df.iterrows():
        if pd.isna(r["_blk"]):
            continue
        by_block.setdefault(int(r["_blk"]), []).append(r.to_dict())

    curriculum = Path(args.curriculum_csv)
    cbt_by_block = _cbt_components_by_block(curriculum, _live_courseid_by_name(Path(args.cbt_extract)))
    cbt_source = (str(curriculum.relative_to(REPO)) if curriculum.is_relative_to(REPO) else str(curriculum)) \
        if cbt_by_block else f"none found ({curriculum})"

    n_comp = n_gate = n_cbt = 0
    for block in p["blocks"]:
        order = int(block["order"])
        subs = by_block.get(order, [])
        comps, e2e = [], []
        # CBTs first (learn), then sims (practice), then the test_call gate falls out of sims.
        cbt_comps = cbt_by_block.get(order, [])
        n_cbt += len(cbt_comps)
        for s in subs:
            mode = _clean(s.get("Simulation Mode"))
            kind = "test_call" if mode == _E2E else "skill_sim"
            comp = {"kind": kind, "ref": _sim_ref(s.get("SIM ID"))}
            persona = _snake(s.get("Name"))
            if persona:
                comp["persona"] = persona          # forward-looking TA challenge_id (crosswalk pending)
            if _clean(s.get("Topic")):
                comp["topic"] = _clean(s.get("Topic"))
            if mode:
                comp["mode"] = mode
            comp["develops"] = []                  # TODO: link once ASC-SIM <-> TA challenge_id crosswalk exists
            comps.append(comp)
            if kind == "test_call":
                e2e.append(s)
        merged = cbt_comps + comps
        if merged:
            block["components"] = merged
            n_comp += len(comps)
        gate = block.get("gate") or {}
        if e2e:
            gate["test_call"] = _sim_ref(_pick_gate(e2e).get("SIM ID"))
            n_gate += 1
        block["gate"] = gate

    header = (
        "# configs/training/training_program.yaml\n"
        "# ============================================================================\n"
        "# ASCEND \"Launchpad\" program structure -- learning blocks + curriculum routing.\n"
        "# ============================================================================\n"
        "# Block metadata (order/label/expected_completion_day/develops/gate.pass_mark) is\n"
        "# hand-curated; `components` and `gate.test_call` are merged by tools/gen_program_components.py:\n"
        "#   - sims: docs/training/\"Ascend Simulations.xlsx\" (sheet \"Sims 100\") -> kind: skill_sim | test_call\n"
        "#   - CBTs: the curriculum course export (its Modality == CBT rows)     -> kind: cbt\n"
        "#\n"
        "# CROSSWALK GAP: sim `ref` is the Ascend SIM ID and `persona` is the design persona;\n"
        "# neither matches the live TA data's challenge_ids yet (0/93 overlap). CBT `ref` is the live\n"
        "# `cbt_<courseid>` metric key when the course's Workday link carries a GUID (links straight to\n"
        "# the scored metric + drives the dashboard's CBT->block nesting), else a name slug (curriculum-\n"
        "# only, pending a courseid). `develops` stays block-level (best-effort seed) until then.\n"
        "# Regenerate components with: python tools/gen_program_components.py\n"
        "# ============================================================================\n\n"
    )

    n_cbt_blocks = sum(1 for b in p["blocks"]
                       if any(c.get("kind") == "cbt" for c in (b.get("components") or [])))
    n_sim_blocks = sum(1 for b in p["blocks"]
                       if any(c.get("kind") in ("skill_sim", "test_call") for c in (b.get("components") or [])))
    summary = (f"sims: {n_comp} components across {n_sim_blocks} blocks; gate.test_call on {n_gate} blocks. "
               f"cbts: {n_cbt} components across {n_cbt_blocks} blocks (source: {cbt_source}).")

    if args.dry_run:
        print("DRY RUN -- no file written.")
        print(summary)
        sample = next((b for b in p["blocks"]
                       if any(c.get("kind") == "cbt" for c in (b.get("components") or []))), None)
        if sample is not None:
            print(f"\nsample block_{int(sample['order']):02d} cbt components:")
            for c in sample["components"]:
                if c.get("kind") == "cbt":
                    print("   ", c)
        return 0

    with PROGRAM.open("w", encoding="utf-8", newline="\n") as f:
        f.write(header)
        yaml.safe_dump(prog, f, sort_keys=False, default_flow_style=False, allow_unicode=True, width=100)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
