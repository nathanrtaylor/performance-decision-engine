"""SIMULATED training dashboard for review — real roster, synthesized progress.

Uses the REAL class roster (who / class / trainer / start date) and the REAL block
schedule (expected_completion_day), but SYNTHESIZES per-expert activity (sims/CBTs) and
skill scores so the dashboard shows the full range we'd expect mid-program: ahead /
on-track / behind on pace, plus retraining, in-training, completed, and not-started.
Current block is inferred from the synthesized activity (no roster progress feed).
Clearly labeled SIMULATED — not real performance data.

    python tools/gen_training_review_dashboard.py [--report-date YYYY-MM-DD] [--seed N] [--out PATH]

Default output: outputs/training_runs/review_<report-date>/training_dashboard.{html,json} (git-ignored).
"""
from __future__ import annotations

import argparse
import random
from collections import Counter
from pathlib import Path

import pandas as pd
import yaml

from pde.training.program import load_program, load_policy, build_skill_routing
from pde.training.roster import load_class_roster
from pde.reporting.training_dashboard import build_training_records, write_training_dashboard

REPO = Path(__file__).resolve().parent.parent
NOTICE = ("SIMULATED for review — real roster + expected dates; per-expert progress and skill "
          "scores are synthesized to illustrate the range (ahead / on-track / behind / retraining / "
          "not-started). NOT real performance data.")


def _skill_ids() -> list:
    mc = yaml.safe_load((REPO / "configs/training/mappings/metric_catalog.yaml").read_text(encoding="utf-8"))
    mc = mc.get("metric_catalog", mc)
    return [m for m, meta in (mc.get("metrics") or {}).items() if (meta or {}).get("source") == "training_assist_skills"]


def _passing_activity(program, aid: str, upto: int, rd: str):
    """Synthetic activity satisfying every component in blocks 1..upto (matching component refs),
    so those blocks read 'passed' and current_block = upto. Returns (sim_rows, cbt_rows).
    Progress is inferred from activity now -- there is no roster current_block_order feed."""
    sims, cbts = [], []
    for b in program.blocks:
        if b.order > upto:
            break
        for c in b.components:
            if not c.ref:
                continue
            if c.kind == "cbt":
                cbts.append({"agent_id": aid, "courseid": c.ref, "coursename": c.ref, "ref": c.ref,
                             "block_num": b.order, "scored": False, "completed": True,
                             "sessions": 1, "pass_rate": None, "last_period": rd})
            else:
                sims.append({"agent_id": aid, "challenge_id": c.ref, "label": c.ref, "sim_id": c.ref,
                             "block_num": b.order, "sessions": 1, "pass_rate": 0.95, "last_period": rd})
    return sims, cbts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--roster", default=str(REPO / "data/training/training_class_roster.xlsx"))
    ap.add_argument("--report-date", default="2026-09-02")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    rnd = random.Random(args.seed)

    program = load_program(REPO / "configs/training/training_program.yaml")
    policy = load_policy(REPO / "configs/training/remediation.yaml")
    prof = yaml.safe_load((REPO / "configs/training/training_profiles.yaml").read_text(encoding="utf-8"))
    cats = prof.get("skill_categories") or {}
    skill_meta = {sid: {"label": (m or {}).get("label") or sid,
                        "category": (cats.get((m or {}).get("category")) or {}).get("label", (m or {}).get("category") or "")}
                  for sid, m in (prof.get("skills") or {}).items()}
    skill_ids = _skill_ids()

    routing = build_skill_routing(program)
    order = {b.id: b.order for b in program.blocks}
    first_order = {s: order[routing[s][0]] for s in skill_ids if routing.get(s)}

    def expected_block(days: int) -> int:
        due = [b.order for b in program.blocks
               if b.expected_completion_day is not None and b.expected_completion_day <= days]
        return max(due) if due else 1

    roster = load_class_roster(args.roster)
    report = pd.Timestamp(args.report_date)

    last_num = max(b.order for b in program.blocks)
    sk_rows, sim_rows, cbt_rows, completed = [], [], [], 0
    for aid, row in roster.set_index("agent_id").iterrows():
        try:
            dss = int((report - pd.Timestamp(row["training_start_date"]).normalize()).days)
        except Exception:  # noqa: BLE001
            dss = 9
        eb = expected_block(dss)

        # a few COMPLETED experts (every block's components passed) -> completion hand-off report
        if completed < 3 and rnd.random() < 0.05:
            s, c = _passing_activity(program, aid, last_num, args.report_date)
            sim_rows += s; cbt_rows += c
            for sid in skill_ids:                                # data for every block, all passing
                sk_rows.append({"agent_id": aid, "metric": sid,
                                "value": round(rnd.uniform(0.85, 0.98), 3), "benchmark": 0.80})
            completed += 1
            continue

        roll = rnd.random()
        if roll < 0.12:            # ~12% haven't produced activity yet -> not_started (no activity, no skills)
            continue
        # current block inferred from activity around the expected block -> ahead / on / behind
        offset = rnd.choice([-3, -2, -1, -1, 0, 0, 1, 1, 2])
        current = min(15, max(1, eb + offset))
        s, c = _passing_activity(program, aid, current, args.report_date)
        sim_rows += s; cbt_rows += c

        remediate = rnd.random() < 0.32
        base = rnd.uniform(0.84, 0.95)
        for sid in skill_ids:
            if first_order.get(sid, 99) > current:      # only skills in blocks reached so far
                continue
            sk_rows.append({"agent_id": aid, "metric": sid,
                            "value": round(min(0.99, max(0.55, base + rnd.uniform(-0.06, 0.05))), 3),
                            "benchmark": 0.80})
        if remediate:
            reached = [s for s in skill_ids if first_order.get(s, 99) <= current]
            rnd.shuffle(reached)
            lowered = {s for s in reached[: rnd.choice([1, 2])]}
            for r in sk_rows:
                if r["agent_id"] == aid and r["metric"] in lowered:
                    r["value"] = round(rnd.uniform(0.55, 0.72), 3)

    # SIMULATED prior coaching history for ~30% of experts, so the "Recent coaching history"
    # block is demonstrable in the review (real runs read the extract's coaching_history.csv).
    _CTYPES = ["ADAPT", "Growth Plan", "In The Game"]
    _BEH = ["Show Compassion", "Drive Results", "One Call Resolution",
            "Use Clear Transition Statements", "Ask for the Sale"]
    ch_rows = []
    for aid in roster["agent_id"]:
        if rnd.random() >= 0.30:
            continue
        for i in range(rnd.choice([1, 2, 3, 4])):
            day = pd.Timestamp("2026-08-20") - pd.Timedelta(days=rnd.randint(0, 150))
            ch_rows.append({"agent_id": aid, "coaching_date": day.strftime("%Y-%m-%d"),
                            "coaching_type": rnd.choice(_CTYPES), "behavior_selected": rnd.choice(_BEH),
                            "coaching_status": rnd.choice(["Submitted", "Submitted", "Excused"])})
    coaching_history = pd.DataFrame(ch_rows) if ch_rows else None

    records, meta = build_training_records(pd.DataFrame(sk_rows), roster, program, policy, skill_meta,
                                           report_date=args.report_date, pass_mark=0.80,
                                           coaching_history=coaching_history,
                                           sims_taken=pd.DataFrame(sim_rows),
                                           cbts_taken=pd.DataFrame(cbt_rows))
    meta["notice"] = NOTICE
    meta["notice_badge"] = "SIMULATED — REVIEW"

    out = Path(args.out) if args.out else REPO / f"outputs/training_runs/review_{args.report_date}"
    path = write_training_dashboard(out, records, meta)
    print(f"wrote {path}")
    print("status:", dict(Counter(r["status"] for r in records)))
    print("pace  :", dict(Counter(str(r["pace"]) for r in records)))
    print("experts:", len(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
