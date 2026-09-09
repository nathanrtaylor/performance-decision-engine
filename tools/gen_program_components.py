"""Enhance training_program.yaml with the Ascend Simulations curriculum.

Merges docs/training/"Ascend Simulations.xlsx" into the program:
  - SIMS (sheet "Sims 100"): per learning block, adds sim `components`
    (SIM ID + persona + topic + mode) and sets `gate.test_call` from the block's
    End-to-End Call sim.
  - CBTs (sheet "CBTs", or --cbts-csv): per learning block, adds `kind: cbt`
    components (ref + topic + modality, and an optional `courseid` crosswalk to the
    live cbt_<courseid> metric). This source is OPTIONAL -- if the sheet/CSV is
    absent no cbt components are emitted (the framework is a no-op until the CBT<->block
    alignment list is provided). See the CBTs-sheet contract in _load_cbt_rows.

Existing block metadata (order, label, expected_completion_day, develops, gate.pass_mark,
...) is always preserved.

IMPORTANT -- crosswalk gap: the Ascend sim IDs/personas do NOT match the live TA
data's challenge_ids (0/93 overlap), so sim components are the CURRICULUM MAP, not yet
scoring-linked. CBT components mirror this: `ref` is the curriculum id (a name slug) and
`courseid` (when supplied) is the crosswalk to the live cbt_<courseid> metric, analogous
to the profile `sim_id` crosswalk.

    python tools/gen_program_components.py                 # regenerate (writes the file)
    python tools/gen_program_components.py --dry-run        # preview only, no write
    python tools/gen_program_components.py --cbts-csv path  # take CBTs from a CSV instead of the xlsx sheet
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
CBT_MAP_CSV = REPO / "configs/training/cbt_block_map.csv"   # default committed CBT<->block seed

_E2E = "End-to-End Call"
_GATE_PRIORITY = ["readiness", "capstone", "gate", "end-to-end", "mixed queue"]
_CBT_SHEET = "CBTs"


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
def _find_col(cols: list[str], *cands: str) -> str | None:
    """Case-insensitive column lookup among candidate header names."""
    lower = {str(c).strip().lower(): c for c in cols}
    for cand in cands:
        hit = lower.get(cand.strip().lower())
        if hit is not None:
            return hit
    return None


def _load_cbt_rows(sims_xlsx: Path, cbts_csv: str | None) -> tuple[pd.DataFrame | None, str | None]:
    """Load the CBT<->block source (df, source-label), or (None, None) if absent.

    Source priority: --cbts-csv arg  >  the xlsx 'CBTs' sheet  >  the committed seed CBT_MAP_CSV.
    CONTRACT (columns, case-insensitive; only the first two are required):
      - Learning Block   (block number 1-16)          [req]  aliases: Block, LB
      - CBT Name         (course/CBT title)            [req]  aliases: Course, Course Name, Name, Coursename
      - Course ID        (live courseid, if known)     [opt]  aliases: CourseID, courseid  -> ref becomes the live cbt_<id> metric key
      - Modality         (CBT / Video / CBT bundle)    [opt]
      - Topic            (short topic label)           [opt]
    """
    if cbts_csv:
        p = Path(cbts_csv)
        return (pd.read_csv(p), str(p)) if p.exists() else (None, None)
    try:
        return pd.read_excel(sims_xlsx, sheet_name=_CBT_SHEET), f"{sims_xlsx.name}[{_CBT_SHEET}]"
    except (ValueError, FileNotFoundError):  # sheet or workbook missing
        pass
    if CBT_MAP_CSV.exists():
        return pd.read_csv(CBT_MAP_CSV), str(CBT_MAP_CSV.relative_to(REPO))
    return None, None


def _cbt_components_by_block(df: pd.DataFrame) -> dict[int, list[dict]]:
    """block order -> [cbt component dicts], from the CBT source. Empty if columns don't resolve."""
    cols = list(df.columns)
    c_blk = _find_col(cols, "Learning Block", "Block", "LB")
    c_name = _find_col(cols, "CBT Name", "Course Name", "Course", "Coursename", "Name")
    if not c_blk or not c_name:
        raise SystemExit(
            f"CBT source is missing required columns. Need a block column (Learning Block) and a "
            f"name column (CBT Name). Found: {cols}"
        )
    c_cid = _find_col(cols, "Course ID", "CourseID", "courseid")
    c_mod = _find_col(cols, "Modality", "Mode")
    c_topic = _find_col(cols, "Topic")

    out: dict[int, list[dict]] = {}
    for _, r in df.iterrows():
        blk = pd.to_numeric(r.get(c_blk), errors="coerce")
        name = _clean(r.get(c_name))
        if pd.isna(blk) or not name:
            continue
        raw_cid = _clean(r.get(c_cid)) if c_cid else None
        # ref = the live cbt_<courseid> metric key when a Course ID is given (so the component links
        # straight to the scored metric + the dashboard's CBT->block nesting); else a name slug.
        comp: dict = {"kind": "cbt", "ref": _norm_cbt_courseid(raw_cid) or _snake(name)}
        comp["topic"] = _clean(r.get(c_topic)) if (c_topic and _clean(r.get(c_topic))) else name
        comp["modality"] = (_clean(r.get(c_mod)) if c_mod else None) or "CBT"
        comp["develops"] = []  # TODO: link once CBT<->skill alignment is provided
        out.setdefault(int(blk), []).append(comp)
    return out


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="Merge Ascend sims (+ optional CBTs) into training_program.yaml.")
    ap.add_argument("--cbts-csv", default=None,
                    help="Optional CSV of CBT<->block alignments (else the 'CBTs' sheet of the xlsx is used if present).")
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

    cbt_df, cbt_source = _load_cbt_rows(SIMS, args.cbts_csv)
    cbt_by_block = _cbt_components_by_block(cbt_df) if cbt_df is not None else {}

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
        "# hand-curated; `components` and `gate.test_call` are merged from\n"
        "# docs/training/\"Ascend Simulations.xlsx\" by tools/gen_program_components.py:\n"
        "#   - sims  (sheet \"Sims 100\")  -> kind: skill_sim | test_call\n"
        "#   - CBTs  (sheet \"CBTs\")      -> kind: cbt   (optional source; absent = no cbt components)\n"
        "#\n"
        "# CROSSWALK GAP: sim `ref` is the Ascend SIM ID and `persona` is the design persona;\n"
        "# neither matches the live TA data's challenge_ids yet (0/93 overlap). CBT `ref` is the live\n"
        "# `cbt_<courseid>` metric key when a Course ID was supplied (links straight to the scored\n"
        "# metric + drives the dashboard's CBT->block nesting), else a name slug (curriculum-only,\n"
        "# pending a courseid). `develops` stays block-level (best-effort seed) until then.\n"
        "# Regenerate components with: python tools/gen_program_components.py\n"
        "# ============================================================================\n\n"
    )

    n_cbt_blocks = sum(1 for b in p["blocks"]
                       if any(c.get("kind") == "cbt" for c in (b.get("components") or [])))
    n_sim_blocks = sum(1 for b in p["blocks"]
                       if any(c.get("kind") in ("skill_sim", "test_call") for c in (b.get("components") or [])))
    summary = (f"sims: {n_comp} components across {n_sim_blocks} blocks; gate.test_call on {n_gate} blocks. "
               f"cbts: {n_cbt} components across {n_cbt_blocks} blocks "
               f"(source: {cbt_source or 'none found -- no cbt components emitted'}).")

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
