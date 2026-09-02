"""SIMULATED training dashboard for review — real roster, synthesized progress.

Uses the REAL class roster (who / class / trainer / start date) and the REAL block
schedule (expected_completion_day), but SYNTHESIZES a per-expert progress feed
(current_block_order) and skill scores so the dashboard shows the full range we'd
expect mid-program: ahead / on-track / behind on pace, plus retraining, in-training,
and not-started. Clearly labeled SIMULATED — not real performance data.

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

from cde.training.program import load_program, load_policy, build_skill_routing
from cde.training.roster import load_class_roster
from cde.reporting.training_dashboard import build_training_records, write_training_dashboard

REPO = Path(__file__).resolve().parent.parent
NOTICE = ("SIMULATED for review — real roster + expected dates; per-expert progress and skill "
          "scores are synthesized to illustrate the range (ahead / on-track / behind / retraining / "
          "not-started). NOT real performance data.")


def _skill_ids() -> list:
    mc = yaml.safe_load((REPO / "configs/training/mappings/metric_catalog.yaml").read_text(encoding="utf-8"))
    mc = mc.get("metric_catalog", mc)
    return [m for m, meta in (mc.get("metrics") or {}).items() if (meta or {}).get("source") == "training_assist_skills"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--roster", default=str(REPO / "docs/training/training_class_roster.xlsx"))
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
    cbo_by_agent, sk_rows, completed = {}, [], 0
    for aid, row in roster.set_index("agent_id").iterrows():
        try:
            dss = int((report - pd.Timestamp(row["training_start_date"]).normalize()).days)
        except Exception:  # noqa: BLE001
            dss = 9
        eb = expected_block(dss)

        # a few COMPLETED experts (cleared all blocks) -> shows the completion hand-off report
        if completed < 3 and rnd.random() < 0.05:
            cbo_by_agent[aid] = last_num + 1                      # graduated (all blocks passed)
            for sid in skill_ids:                                # data for every block, all passing
                sk_rows.append({"agent_id": aid, "metric": sid,
                                "value": round(rnd.uniform(0.85, 0.98), 3), "benchmark": 0.80})
            completed += 1
            continue

        roll = rnd.random()
        if roll < 0.12:            # ~12% haven't produced data yet -> not_started (no feed, no skills)
            continue
        # progress feed: current block around the expected block -> ahead / on / behind
        offset = rnd.choice([-3, -2, -1, -1, 0, 0, 1, 1, 2])
        current = min(15, max(1, eb + offset))
        cbo_by_agent[aid] = current

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

    roster = roster.copy()
    roster["current_block_order"] = roster["agent_id"].map(cbo_by_agent)   # NaN -> not_started

    records, meta = build_training_records(pd.DataFrame(sk_rows), roster, program, policy, skill_meta,
                                           report_date=args.report_date, pass_mark=0.80)
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
