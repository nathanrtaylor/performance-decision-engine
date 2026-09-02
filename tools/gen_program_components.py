"""Enhance training_program.yaml with the Ascend Simulations curriculum.

Merges docs/training/"Ascend Simulations.xlsx" (sheet "Sims 100") into the program:
per learning block, adds the sim `components` (SIM ID + persona + topic + mode) and
sets `gate.test_call` from the block's End-to-End Call sim. Existing block metadata
(order, label, expected_completion_day, develops, gate.pass_mark, ...) is preserved.

IMPORTANT — crosswalk gap: the Ascend sim IDs/personas do NOT match the live TA
data's challenge_ids (0/93 overlap), so these components are the CURRICULUM MAP,
not yet scoring-linked. `component.persona` is the forward-looking TA join key; an
ASC-SIM <-> live-TA-challenge_id crosswalk (or the new sims going live in TA under
these personas) is required before components drive progress/gate evaluation.

    python tools/gen_program_components.py
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parent.parent
PROGRAM = REPO / "configs/training/training_program.yaml"
SIMS = REPO / "docs/training/Ascend Simulations.xlsx"

_E2E = "End-to-End Call"
_GATE_PRIORITY = ["readiness", "capstone", "gate", "end-to-end", "mixed queue"]


def _snake(x) -> str | None:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    s = re.sub(r"[^0-9a-z]+", "_", str(x).strip().lower()).strip("_")
    return s or None


def _clean(x) -> str | None:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    return re.sub(r"\s+", " ", str(x).strip()) or None


def _pick_gate(e2e: list[dict]) -> dict:
    """Primary gate among a block's End-to-End Call sims."""
    for kw in _GATE_PRIORITY:
        for s in e2e:
            if kw in str(s.get("Topic", "")).lower():
                return s
    return e2e[0]


def main() -> int:
    prog = yaml.safe_load(PROGRAM.read_text(encoding="utf-8"))
    p = prog["program"]

    df = pd.read_excel(SIMS, sheet_name="Sims 100")
    df["_blk"] = pd.to_numeric(df["Learning Block"], errors="coerce")
    by_block: dict[int, list[dict]] = {}
    for _, r in df.iterrows():
        if pd.isna(r["_blk"]):
            continue
        by_block.setdefault(int(r["_blk"]), []).append(r.to_dict())

    n_comp = n_gate = 0
    for block in p["blocks"]:
        subs = by_block.get(int(block["order"]), [])
        comps, e2e = [], []
        for s in subs:
            mode = _clean(s.get("Simulation Mode"))
            kind = "test_call" if mode == _E2E else "skill_sim"
            comp = {"kind": kind, "ref": _clean(s.get("SIM ID"))}
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
        if comps:
            block["components"] = comps
            n_comp += len(comps)
        gate = block.get("gate") or {}
        if e2e:
            gate["test_call"] = _clean(_pick_gate(e2e).get("SIM ID"))
            n_gate += 1
        block["gate"] = gate

    header = (
        "# configs/training/training_program.yaml\n"
        "# ============================================================================\n"
        "# ASCEND \"Launchpad\" program structure -- learning blocks + curriculum routing.\n"
        "# ============================================================================\n"
        "# Block metadata (order/label/expected_completion_day/develops/gate.pass_mark) is\n"
        "# hand-curated; `components` and `gate.test_call` are merged from\n"
        "# docs/training/\"Ascend Simulations.xlsx\" (sheet Sims 100) by\n"
        "# tools/gen_program_components.py. Component `kind`: skill_sim | test_call | cbt.\n"
        "#\n"
        "# CROSSWALK GAP: sim `ref` is the Ascend SIM ID and `persona` is the design persona;\n"
        "# neither matches the live TA data's challenge_ids yet (0/93 overlap). So components\n"
        "# are the CURRICULUM MAP, not yet scoring-linked -- an ASC-SIM <-> live-TA-challenge_id\n"
        "# crosswalk (or the new sims going live in TA) is needed before they drive progress /\n"
        "# gate evaluation. `develops` stays block-level (best-effort seed) until then.\n"
        "# Regenerate components with: python tools/gen_program_components.py\n"
        "# ============================================================================\n\n"
    )
    with PROGRAM.open("w", encoding="utf-8", newline="\n") as f:
        f.write(header)
        yaml.safe_dump(prog, f, sort_keys=False, default_flow_style=False, allow_unicode=True, width=100)

    n_e2e_blocks = sum(1 for b in p["blocks"] if (b.get("gate") or {}).get("test_call")
                       and str((b.get("gate") or {}).get("test_call")).startswith("ASC-SIM"))
    print(f"merged {n_comp} sim components across {sum(1 for b in p['blocks'] if b.get('components'))} blocks; "
          f"set gate.test_call from End-to-End Call on {n_gate} blocks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
