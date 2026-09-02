"""Generate a SHAREABLE, anonymized demo of the ASCEND Launchpad training dashboard.

Synthesizes an illustrative cohort (no real PII) and renders it through the real
`build_training_records` + dashboard, so the demo exercises the actual engine/UI:

  - Launchpad runs ~3 weeks; classes start together by CLASS ID on staggered weeks,
    so different classes sit at different points in the program (some near done,
    some just starting).
  - Per-expert progress + skill scores are designed to show the full range:
    ahead of pace / on track / behind, plus on-track (no remediation), needs-
    remediation, and completed.

    python tools/gen_training_demo_dashboard.py [--out PATH] [--report-date YYYY-MM-DD] [--seed N]

Output default (a shareable artifact, safe to circulate): docs/training/training_dashboard_shareable.html
"""
from __future__ import annotations

import argparse
import random
from collections import Counter
from pathlib import Path

import pandas as pd
import yaml

from pde.training.program import load_program, load_policy, build_skill_routing
from pde.reporting.training_dashboard import build_training_records, render_html
from pde.cli.run_training_pipeline import _skill_meta

REPO = Path(__file__).resolve().parent.parent


def _skill_ids() -> list:
    mc = yaml.safe_load((REPO / "configs/training/mappings/metric_catalog.yaml").read_text(encoding="utf-8"))
    mc = mc.get("metric_catalog", mc)
    return list((mc.get("metrics") or {}).keys())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(REPO / "docs/training/training_dashboard_shareable.html"))
    ap.add_argument("--report-date", default="2026-09-01")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)
    rnd = random.Random(args.seed)

    program = load_program(REPO / "configs/training/training_program.yaml")
    policy = load_policy(REPO / "configs/training/remediation.yaml")
    skill_meta = _skill_meta(REPO / "configs/training/training_profiles.yaml")
    skill_ids = _skill_ids()

    routing = build_skill_routing(program)
    order_by_block = {b.id: b.order for b in program.blocks}
    first_order = {s: order_by_block[routing[s][0]] for s in skill_ids if s in routing}

    def expected_block(days: int) -> int:
        due = [b.order for b in program.blocks
               if b.expected_completion_day is not None and b.expected_completion_day <= days]
        return max(due) if due else 0

    report = pd.Timestamp(args.report_date)
    # (class id, start date, size, trainer) -- classes start on staggered Mondays across the 3-week window
    classes = [
        ("TRN-A", "2026-08-11", 10, "M. Diaz"),   # ~21 days in -> near the end
        ("TRN-B", "2026-08-18", 11, "P. Shah"),    # ~14 days in -> mid program
        ("TRN-C", "2026-08-25", 9, "J. Cole"),     # ~7 days in  -> early
        ("TRN-D", "2026-08-29", 9, "R. Vega"),     # ~3 days in  -> just started
    ]

    sk_rows, ag_rows = [], []
    n = 0
    for cid, start, size, trainer in classes:
        dss = int((report - pd.Timestamp(start)).days)
        eb = expected_block(dss)
        for _ in range(size):
            n += 1
            aid = f"NH-{n:05d}"
            offset = rnd.choice([-2, -1, 0, 0, 1, 1, 2])       # weighted toward on/ahead of pace
            cur = min(16, max(1, eb + offset))
            base = rnd.uniform(0.86, 0.96)
            vals = {s: round(min(0.99, max(0.55, base + rnd.uniform(-0.06, 0.04))), 3) for s in skill_ids}
            if rnd.random() < 0.30:                            # ~30% need remediation
                reached = [s for s in skill_ids if first_order.get(s, 99) <= cur]
                rnd.shuffle(reached)
                for s in reached[: rnd.choice([1, 2])]:
                    vals[s] = round(rnd.uniform(0.55, 0.72), 3)  # clearly below the 80% mark
            for s, v in vals.items():
                sk_rows.append({"agent_id": aid, "metric": s, "value": v, "benchmark": 0.80})
            ag_rows.append({"agent_id": aid, "agent_name": f"Expert {n:05d}", "class_id": cid,
                            "trainer": trainer, "icp_client": "training",
                            "training_start_date": start, "current_block_order": cur})

    records, meta = build_training_records(pd.DataFrame(sk_rows), pd.DataFrame(ag_rows),
                                           program, policy, skill_meta,
                                           report_date=args.report_date, pass_mark=0.80)
    meta["roster_fields_synthetic"] = True    # honest: this is illustrative/anonymized data
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(records, meta), encoding="utf-8")

    print(f"wrote {out} ({out.stat().st_size/1024:.0f} KB) — {len(records)} experts")
    print("status:", dict(Counter(r["status"] for r in records)))
    print("pace:  ", dict(Counter(r["pace"] for r in records)))
    print("avg days in program:", round(sum(r["days_since_start"] for r in records) / len(records), 1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
