"""Example ASCEND new-hire training dashboard — STATIC MOCKUP with fabricated data.

This is a self-contained example/prototype of a trainer-facing dashboard for the ASCEND
new-hire training program. It is NOT wired into the pipeline and reads NO real data: the
sample new hires, scores, and re-training flags below are entirely fabricated to illustrate
the concept and layout.

It mirrors the look-and-feel of the production expert dashboard
(``src/pde/reporting/expert_dashboard.py``): the same neutral palette, status colors, card
grid + single reusable modal, and light/dark toggle.

The ASCEND framing shown here (CBTs / skill-based scenarios / test calls, learning blocks,
and the re-training loop) does not yet exist in the repo — it is introduced for this mockup.
Real vocabulary is borrowed for authenticity: skill/category labels come from
``configs/training/training_profiles.yaml`` and scores are expressed as the pass-rate the
TrAIning Assist pipeline uses (``calc``, 0-1). Roster-style fields (name, class/mascot,
trainer/coach, tenure) mirror ``data/raw/weekly/latest/agents.csv``.

It also emits a UI-agnostic **JSON data export** (``build_export`` / ``to_export_record``): a
clean, tagged per-expert schema containing only what the dashboard shows, derived from the same
sample records so the two never drift. A real UI would render straight from this JSON.

Usage:
    python tools/ascend_training_dashboard_example.py [--out PATH] [--json PATH]

Default outputs (both git-ignored):
    outputs/examples/ascend_training_dashboard_example.html
    outputs/examples/ascend_training_dashboard_example.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------- #
# Mockup constants (fabricated)
# --------------------------------------------------------------------------- #
PASS_BENCHMARK = 0.80          # example test-call / behavior pass mark
BLOCKS = ["Phase 1", "Phase 2", "Phase 3", "Final Assessment"]

# (skill label, category label) — real names from configs/training/training_profiles.yaml,
# curated to a typical NH sales/service profile's "always" requirements.
SKILLS: List[tuple] = [
    ("Warm Greeting", "Customer Connection"),
    ("Show Empathy", "Customer Connection"),
    ("Reassure the Customer", "Customer Connection"),
    ("Verification", "Call Control"),
    ("Account Assessment", "Technical Discovery"),
    ("Set Expectations", "Call Control"),
    ("Advanced Discovery", "Technical Discovery"),
    ("Confirm / Test Resolution", "Resolution"),
    ("Transition Statement", "Sales Transition"),
    ("Core Benefit 1", "Value Presentation"),
    ("Price Statement", "Value Presentation"),
    ("Ask for the Sale", "Closing"),
    ("Resolve Customer Concerns", "Objection Handling"),
    ("Conversational Language", "Sales Delivery"),
    ("Thank & Reinforce", "Call Close"),
    ("Goodbye With Rapport", "Call Close"),
]
# Skills a struggling hire tends to miss — used to justify a re-training flag.
RETRAIN_TARGETS = {"Ask for the Sale", "Price Statement"}

# (name, class, trainer, current_block_idx, status, base_competence)
_SEEDS = [
    ("Ava Thompson", "falcons trn", "Morgan Diaz", 3, "completed", 0.93),
    ("Liam Carter", "falcons trn", "Morgan Diaz", 2, "on_track", 0.88),
    ("Noah Rivera", "falcons trn", "Morgan Diaz", 1, "on_track", 0.84),
    ("Mia Nguyen", "falcons trn", "Morgan Diaz", 2, "retraining", 0.76),
    ("Ethan Brooks", "falcons trn", "Morgan Diaz", 0, "on_track", 0.82),
    ("Sofia Reyes", "raptors trn", "Priya Shah", 3, "completed", 0.91),
    ("Jackson Lee", "raptors trn", "Priya Shah", 2, "on_track", 0.86),
    ("Olivia Martin", "raptors trn", "Priya Shah", 1, "retraining", 0.74),
    ("Lucas Kim", "raptors trn", "Priya Shah", 1, "on_track", 0.83),
    ("Emma Davis", "raptors trn", "Priya Shah", 3, "retraining", 0.78),
]


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _off(i: int, j: int) -> float:
    """Deterministic small +/- offset (-0.10..+0.10) so scores vary but output is stable."""
    return (((i * 29 + j * 13) % 21) - 10) / 100.0


def _r2(x: float) -> float:
    return round(x, 2)


def _behaviors(hi: int, bidx: int, base: float, failing: bool) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for j, (label, cat) in enumerate(SKILLS):
        v = _clamp(base + 0.02 * bidx + _off(hi, j), 0.45, 0.99)
        if failing and label in RETRAIN_TARGETS:
            v = _clamp(0.58 + _off(hi, j) / 2, 0.45, 0.72)   # clearly below the pass mark
        out.append({"skill": label, "cat": cat, "score": _r2(v)})
    return out


def _mean(rows: List[Dict[str, Any]]) -> float:
    return _r2(sum(r["score"] for r in rows) / len(rows)) if rows else 0.0


def _block(hi: int, bidx: int, base: float, status: str) -> Dict[str, Any]:
    """One learning block with its three modalities. status: passed|in_progress|retraining|locked."""
    if status == "locked":
        return {"name": BLOCKS[bidx], "status": "locked"}

    cbt = {"attempts": 1 + ((hi + bidx) % 3), "score": 1.0, "passed": True}
    scn_score = _clamp(base + 0.02 * bidx + _off(hi, bidx + 5), 0.6, 0.99)
    scenario = {"attempts": 2 + ((hi + bidx) % 4), "score": _r2(scn_score), "passed": True}

    test: Dict[str, Any]
    if status == "in_progress":
        test = {"taken": False}                          # test call not attempted yet
    else:
        failing = status == "retraining"
        beh = _behaviors(hi, bidx, base, failing)
        overall = _mean(beh)
        if failing:
            overall = min(overall, _r2(PASS_BENCHMARK - 0.03 - _off(hi, bidx) / 5))
        else:
            overall = max(overall, PASS_BENCHMARK)       # passed blocks clear the mark
        test = {"taken": True, "attempts": 1, "overall": _r2(_clamp(overall, 0.5, 0.99)),
                "behaviors": beh}
    return {"name": BLOCKS[bidx], "status": status, "cbt": cbt, "scenario": scenario, "test": test}


def _hire(hi: int, seed: tuple) -> Dict[str, Any]:
    name, cls, trainer, cur, status, base = seed
    cur_status = {"completed": "passed", "on_track": "in_progress", "retraining": "retraining"}[status]

    blocks: List[Dict[str, Any]] = []
    for b in range(len(BLOCKS)):
        if b < cur:
            bs = "passed"
        elif b == cur:
            bs = cur_status
        else:
            bs = "locked"
        blocks.append(_block(hi, b, base, bs))

    # Latest test-call score = the most recent block that has a taken test.
    latest: Optional[float] = None
    for blk in blocks:
        t = blk.get("test")
        if isinstance(t, dict) and t.get("taken"):
            latest = t.get("overall")

    # Aggregate per-skill pass-rates (what a trainer scans for weak behaviors).
    failing = status == "retraining"
    skills = _behaviors(hi, cur, base, failing)

    retrain = None
    if status == "retraining":
        cur_test = blocks[cur].get("test", {})
        low = sorted(cur_test.get("behaviors", []), key=lambda r: r["score"])[:2]
        focus = [r["skill"] for r in low]
        b1 = focus[0] if focus else "the focus behaviors"
        b2 = focus[1] if len(focus) > 1 else b1
        blk = BLOCKS[cur]
        retrain = {
            "block": blk,
            "reason": f"Test call {round((cur_test.get('overall') or 0)*100)}% below the "
                      f"{round(PASS_BENCHMARK*100)}% pass mark",
            "behaviors": focus,
            # Fabricated, role-split recommendations. Expert = the new hire's own next steps;
            # trainer/coach = the support each provides around the re-try.
            "expert_actions": [
                f"Re-run the {blk} skill-based scenarios until passing (unlimited attempts)",
                f"Re-take the CBT refresher on {b1} and {b2}",
                "Attempt a fresh test call once scenarios are passing",
            ],
            "trainer_actions": [
                "Add to the next re-try group (formed near the end of the block)",
                f"Observe a practice call and give targeted feedback on {b1}",
                "Confirm CBT and scenario completion before the re-test",
            ],
            "coach_actions": [
                f"Hold a 1:1 to reinforce {b2}",
                f"Share a model-call example demonstrating {b1}",
                "Review the re-test result and update the re-training status",
            ],
        }

    return {
        "id": str(700101 + hi),
        "name": name,
        "cls": cls,
        "trainer": trainer,
        "icp": "mob-verizon",
        "tenure": "0-30",
        "block": BLOCKS[cur],
        "status": status,
        "test_call": latest,
        "blocks": blocks,
        "skills": skills,
        "retrain": retrain,
    }


def build_sample() -> List[Dict[str, Any]]:
    return [_hire(i, s) for i, s in enumerate(_SEEDS)]


def build_meta() -> Dict[str, Any]:
    return {
        "program": "ASCEND New-Hire Training",
        "bench": PASS_BENCHMARK,
        "blocks": BLOCKS,
        # A fixed illustrative timestamp (kept static so regenerated output is stable).
        "generated": "2026-08-19 09:00",
    }


def render_html(hires: List[Dict[str, Any]], meta: Dict[str, Any]) -> str:
    data_json = json.dumps(hires, ensure_ascii=False, separators=(",", ":"))
    meta_json = json.dumps(meta, ensure_ascii=False)
    return HTML_TEMPLATE.replace("__DATA__", data_json).replace("__META__", meta_json)


# --------------------------------------------------------------------------- #
# JSON data export — the UI-agnostic data contract
#
# A clean, self-describing per-expert schema containing ONLY what the dashboard
# shows, derived from the same sample records the HTML renders (so the two never
# drift). Values are "tagged": scores carry {metric,value,display[,passed]};
# statuses carry {code,label,severity}; re-training actions are grouped by role
# with an explicit `support` flag. A real UI would render straight from this.
# --------------------------------------------------------------------------- #
SCHEMA_VERSION = "1.0"

_STATUS_TAG = {
    "on_track": ("On track", "good"),
    "retraining": ("Re-training needed", "serious"),
    "completed": ("Completed", "good"),
}
_BLOCK_TAG = {
    "passed": ("Passed", "good"),
    "in_progress": ("In progress", "muted"),
    "retraining": ("Re-training", "serious"),
    "locked": ("Locked", "muted"),
}


def _tag(mapping: Dict[str, tuple], code: str) -> Dict[str, str]:
    label, severity = mapping.get(code, (code, "muted"))
    return {"code": code, "label": label, "severity": severity}


def _score(v: Optional[float], with_pass: bool = False) -> Optional[Dict[str, Any]]:
    if v is None:
        return None
    obj: Dict[str, Any] = {"metric": "pass_rate", "value": v, "display": f"{round(v*100)}%"}
    if with_pass:
        obj["passed"] = v >= PASS_BENCHMARK
    return obj


def _modality_export(m: Optional[Dict[str, Any]], label: str, kind: str) -> Optional[Dict[str, Any]]:
    if m is None:
        return None
    if kind == "test":
        if not m.get("taken"):
            return {"modality": label, "taken": False}
        return {
            "modality": label, "taken": True, "attempts": m.get("attempts"),
            "overall": _score(m.get("overall"), with_pass=True),
            "behaviors": [
                {"skill": b["skill"], "category": b["cat"],
                 "score": _score(b["score"]), "below_benchmark": b["score"] < PASS_BENCHMARK}
                for b in m.get("behaviors", [])
            ],
        }
    return {"modality": label, "attempts": m.get("attempts"),
            "score": _score(m.get("score")), "passed": bool(m.get("passed"))}


def to_export_record(h: Dict[str, Any]) -> Dict[str, Any]:
    """Map one internal sample hire to the tagged, UI-agnostic export schema."""
    modalities = []
    for b in h["blocks"]:
        if b["status"] == "locked":
            modalities.append({"block": b["name"], "block_status": _tag(_BLOCK_TAG, "locked"),
                               "cbt": None, "skill_based_scenarios": None, "test_call": None})
            continue
        modalities.append({
            "block": b["name"],
            "block_status": _tag(_BLOCK_TAG, b["status"]),
            "cbt": _modality_export(b.get("cbt"), "CBTs", "graded"),
            "skill_based_scenarios": _modality_export(b.get("scenario"), "Skill-based scenarios", "graded"),
            "test_call": _modality_export(b.get("test"), "Test call", "test"),
        })

    rec: Dict[str, Any] = {
        "id": h["id"],
        "identity": {
            "name": h["name"], "class": h["cls"], "trainer": h["trainer"],
            "icp_client": h["icp"], "tenure_group": h["tenure"],
        },
        "status": _tag(_STATUS_TAG, h["status"]),
        "current_block": h["block"],
        "latest_test_call": _score(h["test_call"], with_pass=True),
        "learning_blocks": [
            {"name": b["name"], "status": _tag(_BLOCK_TAG, b["status"])} for b in h["blocks"]
        ],
        "modalities": modalities,
        "skill_scores": [
            {"skill": s["skill"], "category": s["cat"], "score": _score(s["score"]),
             "below_benchmark": s["score"] < PASS_BENCHMARK}
            for s in h["skills"]
        ],
        "retraining": None,
    }
    if h.get("retrain"):
        r = h["retrain"]
        rec["retraining"] = {
            "block": r["block"],
            "reason": r["reason"],
            "focus_behaviors": r["behaviors"],
            "recommendations": {
                "expert": {"role": "expert", "label": "Expert actions",
                           "support": False, "actions": r["expert_actions"]},
                "trainer": {"role": "trainer", "label": "Trainer actions",
                            "support": True, "actions": r["trainer_actions"]},
                "coach": {"role": "coach", "label": "Coach actions",
                          "support": True, "actions": r["coach_actions"]},
            },
        }
    return rec


def build_export(hires: List[Dict[str, Any]], meta: Dict[str, Any]) -> Dict[str, Any]:
    """Full JSON export: envelope + per-expert records."""
    return {
        "schema_version": SCHEMA_VERSION,
        "program": meta["program"],
        "generated": meta["generated"],
        "pass_benchmark": {"metric": "pass_rate", "value": PASS_BENCHMARK,
                           "display": f"{round(PASS_BENCHMARK*100)}%"},
        "experts": [to_export_record(h) for h in hires],
    }


# --------------------------------------------------------------------------- #
# Self-contained HTML (palette + scaffold mirror src/pde/reporting/expert_dashboard.py)
# --------------------------------------------------------------------------- #
HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ASCEND New-Hire Training — Trainer Dashboard (Example)</title>
<style>
:root{
  --surface-1:#fcfcfb; --surface-2:#ffffff; --page:#f9f9f7;
  --text-1:#0b0b0b; --text-2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --border:rgba(11,11,11,.10); --baseline:#c3c2b7;
  --series:#2a78d6; --track:#eef1f5;
  --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
  --warning-ink:#8a6100; --serious-ink:#a2432a;
  --shadow:0 10px 40px rgba(11,11,11,.18);
}
:root[data-theme="dark"]{
  --surface-1:#1a1a19; --surface-2:#201f1e; --page:#0d0d0d;
  --text-1:#fff; --text-2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --border:rgba(255,255,255,.10); --baseline:#383835;
  --series:#3987e5; --track:#242422;
  --warning-ink:#fab219; --serious-ink:#ec835a;
  --shadow:0 10px 44px rgba(0,0,0,.55);
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --surface-1:#1a1a19; --surface-2:#201f1e; --page:#0d0d0d;
    --text-1:#fff; --text-2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --border:rgba(255,255,255,.10); --baseline:#383835;
    --series:#3987e5; --track:#242422;
    --warning-ink:#fab219; --serious-ink:#ec835a;
    --shadow:0 10px 44px rgba(0,0,0,.55);
  }
}
*{box-sizing:border-box}
html{color-scheme:light dark}
body{margin:0;background:var(--page);color:var(--text-1);
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif;font-size:14px;line-height:1.45}
.wrap{max-width:1200px;margin:0 auto;padding:24px 22px 72px}
h1{font-size:22px;margin:0 0 2px}
.sub{color:var(--text-2);font-size:12.5px;margin:0}
.muted{color:var(--muted)}
.topbar{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap}
.topbar .right{display:flex;gap:8px;align-items:center}
.themebtn{background:var(--surface-1);color:var(--text-2);border:1px solid var(--border);
  border-radius:999px;padding:6px 13px;font-size:12.5px;cursor:pointer;font-weight:600}
.themebtn:hover{color:var(--text-1)}
.exbadge{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;font-weight:700;
  padding:4px 11px;border-radius:999px;color:var(--warning-ink);
  border:1px solid color-mix(in srgb,var(--warning) 45%,var(--border));
  background:color-mix(in srgb,var(--warning) 12%,var(--surface-1))}

/* tiles */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0 6px}
.tile{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:14px 16px}
.tile .v{font-size:28px;font-weight:650;letter-spacing:-.01em}
.tile .k{color:var(--text-2);font-size:12px;margin-top:2px}

/* filters */
.filters{display:flex;gap:14px;align-items:flex-end;flex-wrap:wrap;margin:22px 0 4px}
.fgroup{display:flex;flex-direction:column;gap:5px}
.flabel{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:600}
.seg{display:inline-flex;background:var(--surface-1);border:1px solid var(--border);border-radius:9px;padding:3px;gap:2px;flex-wrap:wrap}
.seg button{border:0;background:transparent;color:var(--text-2);padding:6px 11px;border-radius:6px;
  font-size:12.5px;cursor:pointer;font-weight:600;white-space:nowrap}
.seg button:hover{color:var(--text-1)}
.seg button.on{background:var(--series);color:#fff}
select,input[type=search]{background:var(--surface-1);color:var(--text-1);border:1px solid var(--border);
  border-radius:8px;padding:7px 10px;font-size:13px;font-family:inherit;min-width:160px}
input[type=search]{min-width:190px}
.count-note{color:var(--muted);font-size:12px;margin:14px 2px 0}

/* group header */
.grouphdr{display:flex;align-items:center;gap:10px;margin:26px 0 10px}
.grouphdr h2{font-size:14px;margin:0;letter-spacing:.01em}
.grouphdr .gc{color:var(--muted);font-size:12px}
.grouphdr .rule{flex:1;height:1px;background:var(--grid)}

/* card grid */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(258px,1fr));gap:12px}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:11px;padding:13px 14px;
  cursor:pointer;text-align:left;font:inherit;color:inherit;display:flex;flex-direction:column;gap:9px;
  transition:border-color .12s, transform .12s}
.card:hover{border-color:var(--series);transform:translateY(-1px)}
.card:focus-visible{outline:2px solid var(--series);outline-offset:2px}
.card .row1{display:flex;align-items:center;justify-content:space-between;gap:8px}
.card .nm{font-weight:650;font-size:14.5px}
.card .dim{color:var(--text-2);font-size:12px}
.card .foot{display:flex;align-items:center;justify-content:space-between;gap:8px;color:var(--muted);font-size:11.5px}

/* chips */
.chip{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;font-weight:600;
  padding:2px 9px;border-radius:999px;border:1px solid var(--border);white-space:nowrap}
.chip .ico{font-size:10px;line-height:1}
.chip.good{color:var(--good)} .chip.warning{color:var(--warning-ink)}
.chip.serious{color:var(--serious-ink)} .chip.critical{color:var(--critical)}
.chip.good .ico{color:var(--good)} .chip.warning .ico{color:var(--warning)}
.chip.serious .ico{color:var(--serious)} .chip.critical .ico{color:var(--critical)}
.chip.muted{color:var(--muted)}

/* progress dots (learning-block ladder on the card) */
.dots{display:inline-flex;gap:4px}
.dot{width:9px;height:9px;border-radius:999px;background:var(--track);border:1px solid var(--border)}
.dot.passed{background:var(--good);border-color:var(--good)}
.dot.in_progress{background:var(--series);border-color:var(--series)}
.dot.retraining{background:var(--serious);border-color:var(--serious)}

/* modal */
.overlay{position:fixed;inset:0;background:rgba(11,11,11,.5);backdrop-filter:blur(2px);
  display:none;align-items:flex-start;justify-content:center;padding:40px 16px;z-index:50;overflow:auto}
.overlay.open{display:flex}
.modal{background:var(--surface-1);border:1px solid var(--border);border-radius:16px;box-shadow:var(--shadow);
  width:100%;max-width:760px;overflow:hidden}
.mhead{padding:20px 22px 16px;border-bottom:1px solid var(--grid);position:relative}
.mhead .close{position:absolute;top:14px;right:14px;border:1px solid var(--border);background:var(--surface-2);
  color:var(--text-2);width:32px;height:32px;border-radius:8px;cursor:pointer;font-size:16px;line-height:1}
.mhead .close:hover{color:var(--text-1)}
.mhead .eyebrow{font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);font-weight:600}
.mhead h3{margin:3px 0 6px;font-size:20px}
.mhead .dims{display:flex;gap:8px;flex-wrap:wrap;align-items:center;color:var(--text-2);font-size:12.5px}
.mbody{padding:8px 22px 22px;max-height:70vh;overflow:auto}
.focusband{background:var(--surface-2);border:1px solid var(--border);border-radius:12px;
  padding:14px 16px;margin:16px 0 6px}
.focusband .k{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);font-weight:600}
.focusband .topic{font-size:19px;font-weight:650;margin:3px 0 4px}

.sec{margin-top:20px}
.sec > .h{display:flex;align-items:center;gap:8px;font-size:12px;text-transform:uppercase;
  letter-spacing:.04em;color:var(--muted);font-weight:700;margin-bottom:8px}

/* ladder */
.ladder{display:flex;flex-direction:column;gap:6px}
.blk{display:flex;align-items:center;gap:10px;padding:9px 12px;border:1px solid var(--border);
  border-radius:9px;background:var(--surface-2)}
.blk .nm{font-weight:600;min-width:120px}
.blk .arm{flex:1}

/* tables */
.cmtable{width:100%;border-collapse:collapse;font-size:12.5px}
.cmtable th{text-align:left;color:var(--muted);font-weight:600;border-bottom:1px solid var(--grid);
  padding:5px 10px 6px 0}
.cmtable td{border-bottom:1px solid var(--grid);padding:6px 10px 6px 0;vertical-align:top}
.cmtable td.m{color:var(--text-1)}
.cmtable td.num{font-variant-numeric:tabular-nums;text-align:right;padding-right:14px}
.cmtable td.cat{color:var(--muted)}

/* re-training banner */
.retrain{display:flex;flex-direction:column;padding:12px 14px;border-radius:10px;
  border:1px solid color-mix(in srgb,var(--serious) 40%,var(--border));
  background:color-mix(in srgb,var(--serious) 8%,var(--surface-2));font-size:13px}
.retrain .rt-top{display:flex;gap:10px;align-items:flex-start}
.retrain .rt-ico{color:var(--serious);font-size:15px;line-height:1.2}
.retrain b{font-weight:650}
.rt-note{margin-top:10px}
/* role-split action columns: Expert / Trainer (support) / Coach (support) */
.actsgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:12px}
@media(max-width:640px){.actsgrid{grid-template-columns:1fr}}
.actcol{background:var(--surface-1);border:1px solid var(--border);border-radius:9px;padding:10px 12px;border-top-width:3px}
.actcol.expert{border-top-color:var(--series)}
.actcol.trainer{border-top-color:var(--good)}
.actcol.coach{border-top-color:var(--warning)}
.acth{font-size:11.5px;text-transform:uppercase;letter-spacing:.04em;font-weight:700;color:var(--text-1);
  margin-bottom:6px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.acth .sup{font-size:10px;font-weight:600;color:var(--muted);text-transform:none;letter-spacing:0;
  border:1px solid var(--border);border-radius:999px;padding:1px 6px}
.acts{margin:0;padding-left:16px}
.acts li{margin:3px 0;font-size:12.5px;color:var(--text-2)}
.tag{display:inline-block;font-size:11.5px;padding:2px 8px;border-radius:5px;border:1px solid var(--border);
  color:var(--text-2);margin:3px 5px 0 0}
.okline{display:flex;gap:9px;align-items:center;padding:11px 13px;border-radius:10px;font-size:13px;
  border:1px solid color-mix(in srgb,var(--good) 35%,var(--border));
  background:color-mix(in srgb,var(--good) 7%,var(--surface-2));color:var(--text-2)}
.okline .ok-ico{color:var(--good)}
.foot-note{color:var(--muted);font-size:12px;margin-top:40px;border-top:1px solid var(--grid);padding-top:14px}
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div>
      <h1>ASCEND New-Hire Training — Trainer Dashboard</h1>
      <p class="sub" id="subline"></p>
    </div>
    <div class="right">
      <span class="exbadge">● EXAMPLE DATA</span>
      <button class="themebtn" id="themebtn" type="button">◐ Theme</button>
    </div>
  </div>

  <div class="tiles" id="tiles"></div>
  <div class="filters" id="filters"></div>
  <div class="count-note" id="count"></div>
  <div id="results"></div>

  <div class="foot-note" id="foot"></div>
</div>

<div class="overlay" id="overlay" role="dialog" aria-modal="true" aria-labelledby="mTitle">
  <div class="modal" id="modal"></div>
</div>

<script id="hires" type="application/json">__DATA__</script>
<script id="meta" type="application/json">__META__</script>
<script>
"use strict";
const HIRES = JSON.parse(document.getElementById("hires").textContent);
const META  = JSON.parse(document.getElementById("meta").textContent);
const BENCH = META.bench;
const byId = new Map(HIRES.map(h => [h.id, h]));
const SEV_ICON = {good:"✓", warning:"⚠", serious:"▲", critical:"✕", muted:"–"};

function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
function pct(v){return (v==null)?"—":Math.round(v*100)+"%";}
function chip(cls,label){return `<span class="chip ${cls}"><span class="ico">${SEV_ICON[cls]||""}</span>${esc(label)}</span>`;}

const STATUS_META = {
  on_track:   {cls:"good",    label:"On track"},
  retraining: {cls:"serious", label:"Re-training needed"},
  completed:  {cls:"good",    label:"Completed"},
};
function statusChip(s){const m=STATUS_META[s]||{cls:"muted",label:s};return chip(m.cls,m.label);}
function scoreChip(v){
  if(v==null) return chip("muted","not taken");
  if(v>=BENCH) return chip("good",pct(v));
  if(v>=BENCH-0.1) return chip("warning",pct(v));
  return chip("critical",pct(v));
}
const BLK_META = {
  passed:{cls:"good",label:"Passed"}, in_progress:{cls:"muted",label:"In progress"},
  retraining:{cls:"serious",label:"Re-training"}, locked:{cls:"muted",label:"Locked"},
};

/* ---- tiles ---- */
function renderTiles(){
  const n = HIRES.length;
  const on = HIRES.filter(h=>h.status==="on_track").length;
  const rt = HIRES.filter(h=>h.status==="retraining").length;
  const done = HIRES.filter(h=>h.status==="completed").length;
  const scored = HIRES.filter(h=>h.test_call!=null);
  const avg = scored.length ? scored.reduce((a,h)=>a+h.test_call,0)/scored.length : null;
  const tiles = [
    {v:n, k:"New hires in training"},
    {v:on, k:"On track"},
    {v:rt, k:"Re-training needed"},
    {v:done, k:"Completed"},
    {v:pct(avg), k:"Avg test-call score"},
  ];
  document.getElementById("tiles").innerHTML = tiles.map(t=>
    `<div class="tile"><div class="v">${esc(t.v)}</div><div class="k">${esc(t.k)}</div></div>`).join("");
  document.getElementById("subline").textContent =
    `Example · fabricated data · refreshes ~2×/day · pass mark ${Math.round(BENCH*100)}% · generated ${META.generated}`;
  document.getElementById("foot").textContent =
    "Mockup only — all new hires, scores, and re-training flags on this page are fabricated for illustration.";
}

/* ---- filters ---- */
const state = {cls:"__all", trainer:"__all", status:"__all", q:""};
function classes(){return [...new Set(HIRES.map(h=>h.cls))];}
function trainers(){return [...new Set(HIRES.map(h=>h.trainer))];}
function buildFilters(){
  const clsBtns = ["__all",...classes()].map(c=>
    `<button data-k="cls" data-v="${esc(c)}" class="${c===state.cls?"on":""}">${c==="__all"?"All classes":esc(c)}</button>`).join("");
  const stBtns = [["__all","All"],["on_track","On track"],["retraining","Re-training"],["completed","Completed"]].map(([v,l])=>
    `<button data-k="status" data-v="${v}" class="${v===state.status?"on":""}">${l}</button>`).join("");
  const trSel = `<select id="trsel"><option value="__all">All trainers</option>`+
    trainers().map(t=>`<option value="${esc(t)}" ${t===state.trainer?"selected":""}>${esc(t)}</option>`).join("")+`</select>`;
  document.getElementById("filters").innerHTML =
    `<div class="fgroup"><span class="flabel">Class</span><div class="seg" data-seg="cls">${clsBtns}</div></div>`+
    `<div class="fgroup"><span class="flabel">Trainer</span>${trSel}</div>`+
    `<div class="fgroup"><span class="flabel">Status</span><div class="seg" data-seg="status">${stBtns}</div></div>`+
    `<div class="fgroup"><span class="flabel">Search</span><input type="search" id="q" placeholder="new hire name…" value="${esc(state.q)}"></div>`;
  document.querySelectorAll('.seg button').forEach(b=>b.onclick=()=>{state[b.dataset.k]=b.dataset.v;buildFilters();render();});
  document.getElementById("trsel").onchange=e=>{state.trainer=e.target.value;render();};
  const q=document.getElementById("q"); q.oninput=e=>{state.q=e.target.value;render();};
  q.focus(); q.setSelectionRange(q.value.length,q.value.length);
}
function match(h){
  if(state.cls!=="__all" && h.cls!==state.cls) return false;
  if(state.trainer!=="__all" && h.trainer!==state.trainer) return false;
  if(state.status!=="__all" && h.status!==state.status) return false;
  if(state.q && !h.name.toLowerCase().includes(state.q.toLowerCase())) return false;
  return true;
}

/* ---- cards ---- */
function ladderDots(h){
  return `<span class="dots">`+h.blocks.map(b=>`<span class="dot ${b.status}" title="${esc(b.name)}: ${esc((BLK_META[b.status]||{}).label||b.status)}"></span>`).join("")+`</span>`;
}
function card(h){
  const passed = h.blocks.filter(b=>b.status==="passed").length;
  return `<button class="card" data-id="${esc(h.id)}">`+
    `<div class="row1"><span class="nm">${esc(h.name)}</span>${statusChip(h.status)}</div>`+
    `<div class="dim">${esc(h.cls)} · ${esc(h.trainer)}</div>`+
    `<div class="dim">Current: <b>${esc(h.block)}</b> · test call ${pct(h.test_call)}</div>`+
    `<div class="foot">${ladderDots(h)}<span>${passed}/${h.blocks.length} blocks passed</span></div>`+
  `</button>`;
}
function render(){
  const rows = HIRES.filter(match);
  const groups = {};
  rows.forEach(h=>{(groups[h.cls]=groups[h.cls]||[]).push(h);});
  const out = Object.keys(groups).sort().map(cls=>{
    const cards = groups[cls].sort((a,b)=>a.name.localeCompare(b.name)).map(card).join("");
    return `<div class="grouphdr"><h2>${esc(cls)}</h2><span class="gc">${groups[cls].length}</span><span class="rule"></span></div>`+
      `<div class="grid">${cards}</div>`;
  }).join("");
  document.getElementById("results").innerHTML = out || `<p class="muted">No new hires match these filters.</p>`;
  document.getElementById("count").textContent = `${rows.length} of ${HIRES.length} new hires`;
  document.querySelectorAll(".card").forEach(c=>c.onclick=()=>openModal(c.dataset.id));
}

/* ---- modal ---- */
function modalityTable(h){
  const cell = (mod,kind)=>{
    if(!mod) return `<td class="num muted">—</td>`;
    if(kind==="test"){
      if(!mod.taken) return `<td class="num">${chip("muted","not taken")}</td>`;
      return `<td class="num">${scoreChip(mod.overall)}</td>`;
    }
    return `<td class="num">${pct(mod.score)} <span class="muted">(${mod.attempts}×)</span></td>`;
  };
  const body = h.blocks.map(b=>{
    if(b.status==="locked") return `<tr><td class="m">${esc(b.name)}</td><td class="num muted" colspan="3">locked</td></tr>`;
    return `<tr><td class="m">${esc(b.name)} ${chip((BLK_META[b.status]||{}).cls||"muted",(BLK_META[b.status]||{}).label||b.status)}</td>`+
      cell(b.cbt,"cbt")+cell(b.scenario,"scn")+cell(b.test,"test")+`</tr>`;
  }).join("");
  return `<table class="cmtable"><thead><tr>`+
    `<th>Learning block</th><th class="num">CBTs</th><th class="num">Skill-based scenarios</th><th class="num">Test call</th>`+
    `</tr></thead><tbody>${body}</tbody></table>`;
}
function skillsTable(h){
  const body = h.skills.map(s=>{
    const below = s.score < BENCH;
    return `<tr><td class="m">${esc(s.skill)}</td><td class="cat">${esc(s.cat)}</td>`+
      `<td class="num">${pct(s.score)}</td>`+
      `<td>${below?chip("critical","below"):chip("good","pass")}</td></tr>`;
  }).join("");
  return `<table class="cmtable"><thead><tr>`+
    `<th>Behavior / skill</th><th>Category</th><th class="num">Score</th><th>vs. pass mark</th>`+
    `</tr></thead><tbody>${body}</tbody></table>`;
}
function ladder(h){
  return `<div class="ladder">`+h.blocks.map(b=>{
    const m = BLK_META[b.status]||{cls:"muted",label:b.status};
    return `<div class="blk"><span class="nm">${esc(b.name)}</span><span class="arm"></span>${chip(m.cls,m.label)}</div>`;
  }).join("")+`</div>`;
}
function actList(arr){return `<ul class="acts">`+(arr||[]).map(a=>`<li>${esc(a)}</li>`).join("")+`</ul>`;}
function retrainBlock(h){
  if(h.retrain){
    const r=h.retrain;
    const tags = r.behaviors.map(b=>`<span class="tag">${esc(b)}</span>`).join("");
    return `<div class="retrain">`+
      `<div class="rt-top"><span class="rt-ico">${SEV_ICON.serious}</span>`+
        `<div><b>Re-training in ${esc(r.block)}.</b> ${esc(r.reason)}. `+
        `They re-try the skill-based scenarios, then the test call.`+
        `<div style="margin-top:4px">Focus behaviors: ${tags}</div></div></div>`+
      `<div class="actsgrid">`+
        `<div class="actcol expert"><div class="acth">Expert actions</div>${actList(r.expert_actions)}</div>`+
        `<div class="actcol trainer"><div class="acth">Trainer actions <span class="sup">support</span></div>${actList(r.trainer_actions)}</div>`+
        `<div class="actcol coach"><div class="acth">Coach actions <span class="sup">support</span></div>${actList(r.coach_actions)}</div>`+
      `</div>`+
      `<div class="muted rt-note">Re-try groups are formed as close to the end of the block as possible.</div>`+
    `</div>`;
  }
  return `<div class="okline"><span class="ok-ico">${SEV_ICON.good}</span>On track — no re-training required.</div>`;
}
function openModal(id){
  const h = byId.get(id);
  if(!h) return;
  document.getElementById("modal").innerHTML = `
    <div class="mhead">
      <button class="close" id="closeBtn" aria-label="Close">✕</button>
      <div class="eyebrow">New-hire training record · example</div>
      <h3 id="mTitle">${esc(h.name)}</h3>
      <div class="dims">${statusChip(h.status)}<span>#${esc(h.id)}</span><span>${esc(h.cls)}</span>`+
        `<span>trainer ${esc(h.trainer)}</span><span>${esc(h.icp)}</span><span>tenure ${esc(h.tenure)}</span></div>
    </div>
    <div class="mbody">
      <div class="focusband">
        <div class="k">Current learning block</div>
        <div class="topic">${esc(h.block)}</div>
        <div>Latest test call: ${scoreChip(h.test_call)}</div>
      </div>

      <div class="sec"><div class="h">Re-training recommendations</div>${retrainBlock(h)}</div>
      <div class="sec"><div class="h">Learning-block progress</div>${ladder(h)}</div>
      <div class="sec"><div class="h">Modality breakdown</div>${modalityTable(h)}</div>
      <div class="sec"><div class="h">Behavior / skill scores</div>${skillsTable(h)}</div>
    </div>`;
  const ov = document.getElementById("overlay");
  ov.classList.add("open");
  document.getElementById("closeBtn").onclick = closeModal;
  document.getElementById("closeBtn").focus();
  if(location.hash !== "#h="+id) history.replaceState(null,"","#h="+id);
}
function closeModal(){
  document.getElementById("overlay").classList.remove("open");
  if(location.hash.startsWith("#h=")) history.replaceState(null,"","#");
}
document.getElementById("overlay").addEventListener("click", e=>{ if(e.target.id==="overlay") closeModal(); });
document.addEventListener("keydown", e=>{ if(e.key==="Escape") closeModal(); });

/* ---- theme toggle ---- */
(function(){
  const btn=document.getElementById("themebtn");
  btn.onclick=()=>{
    const cur=document.documentElement.getAttribute("data-theme");
    const next = cur==="dark" ? "light" : (cur==="light" ? "dark" : "dark");
    document.documentElement.setAttribute("data-theme",next);
  };
})();

function openFromHash(){
  const m=/^#h=(.+)$/.exec(location.hash);
  if(m && byId.has(m[1])) openModal(m[1]);
}

renderTiles(); buildFilters(); render(); openFromHash();
</script>
</body>
</html>"""


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="outputs/examples/ascend_training_dashboard_example.html",
                    help="Output HTML path (default: outputs/examples/... , git-ignored).")
    ap.add_argument("--json", dest="json_out",
                    default="outputs/examples/ascend_training_dashboard_example.json",
                    help="Per-expert JSON data export path (default: outputs/examples/... , git-ignored).")
    args = ap.parse_args(argv)

    hires = build_sample()
    meta = build_meta()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(hires, meta), encoding="utf-8")
    print(f"Wrote {out}  ({out.stat().st_size/1024:.1f} KB)  — {len(hires)} example new hires")

    jout = Path(args.json_out)
    jout.parent.mkdir(parents=True, exist_ok=True)
    jout.write_text(json.dumps(build_export(hires, meta), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {jout}  ({jout.stat().st_size/1024:.1f} KB)  — per-expert data export (schema v{SCHEMA_VERSION})")


if __name__ == "__main__":
    main()
