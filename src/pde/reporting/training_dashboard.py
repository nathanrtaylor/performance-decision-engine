"""Real, data-backed training dashboard (Phase 4).

Promotes the ASCEND mockup (tools/ascend_training_dashboard_example.py) into a
generator that renders from real pipeline outputs + the program structure +
`plan_remediation`. Per the review feedback it:

  - fills richer PROGRAM-LEVEL summary tiles that recompute live from the filters
    (including time-progress: avg days elapsed + % of experts on track);
  - adds a CLASS ID filter (from the expert roster);
  - shows the CURRENT learning block as "Block N — short description";
  - shows whether each expert is ON TRACK vs the block's expected completion day;
  - groups the behavior/skill data UNDER the learning blocks (not a flat table);
  - frames remediation as Learning / Training-support / Coaching actions.

`build_training_records` is the UI-agnostic data layer; the HTML renders from it.
Reuses the theme-aware palette/scaffold of the coaching expert dashboard.
"""
from __future__ import annotations

import base64
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from pde.explainability.training_templates import build_action_groups, remediation_reason
from pde.reporting.expert_dashboard import coaching_history_map_from_df
from pde.training.program import Program, RemediationPolicy, plan_remediation
from pde.utils.logging import get_logger

log = get_logger(__name__)

SCHEMA_VERSION = "1.3"   # 1.3: per-block `sims` + per-expert `sims_unmapped` / `cbts`
                         # 1.2: per-expert `hist` (recent coaching history)


def _title(s: str) -> str:
    return str(s).replace("_", " ").replace("-", " ").title()


def short_desc(label: str) -> str:
    """A short block description: the part after the last ':' (then after ' - ' Stage prefix)."""
    s = str(label)
    if ":" in s:
        s = s.split(":", 1)[1].strip()
    return s.strip()


def _num(v: Any) -> Optional[float]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Data layer
# --------------------------------------------------------------------------- #
def build_training_records(
    skills_df: pd.DataFrame,       # columns: agent_id, metric, value, benchmark
    agents_df: pd.DataFrame,       # roster: agent_id [, agent_name, class_id, trainer, icp_client,
                                   #          training_start_date, current_block_order]
    program: Program,
    policy: RemediationPolicy,
    skill_meta: Dict[str, Dict[str, str]],   # skill_id -> {label, category}
    report_date: str,
    pass_mark: float = 0.80,
    coaching_history: Optional[pd.DataFrame] = None,   # raw coaching_history frame (unbounded); queried for the cohort
    sims_taken: Optional[pd.DataFrame] = None,         # per-(agent,sim): challenge_id,label,sim_id,block_num,sessions,pass_rate,last_period
    cbts_taken: Optional[pd.DataFrame] = None,         # per-(agent,course): courseid,coursename,sessions,pass_rate,last_period
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    sdf = skills_df.copy()
    sdf["agent_id"] = sdf["agent_id"].astype(str)
    adf = agents_df.copy()
    adf["agent_id"] = adf["agent_id"].astype(str)
    adf = adf.drop_duplicates("agent_id").set_index("agent_id")
    acols = set(adf.columns)

    blocks_meta = [{"num": b.order, "id": b.id, "name": b.label, "short": short_desc(b.label)}
                   for b in program.blocks]
    last_num = max((b.order for b in program.blocks), default=0)
    rdate = pd.Timestamp(report_date).normalize()

    def aget(aid: str, col: str, default=None):
        if aid not in adf.index or col not in acols:
            return default
        v = adf.at[aid, col]
        return default if (v is None or (isinstance(v, float) and pd.isna(v))) else v

    # The roster (agents_df) is the SOURCE OF TRUTH for who appears; skills are
    # left-joined. An expert on the roster with no skill data yet is still shown
    # (status "not_started").
    per_agent = {str(k): v for k, v in sdf.groupby("agent_id")} if not sdf.empty else {}
    any_progress = ("current_block_order" in acols) and bool(adf["current_block_order"].notna().any())

    # Recent coaching history, bounded to the training cohort: the coaching_history frame is
    # unbounded (every coached agent in the window), so query it for only the experts on this
    # class roster, then reuse the coaching dashboard's reshaper (agent_id -> [{ty,tp,dt,st}]).
    roster_ids = {str(x) for x in adf.index}
    ch = coaching_history
    if ch is not None and not ch.empty and "agent_id" in ch.columns:
        ch = ch[ch["agent_id"].astype(str).isin(roster_ids)]
    chmap = coaching_history_map_from_df(ch)

    # Per-expert "sims practiced" and "CBTs completed" (from the raw extract). Each is grouped
    # agent_id -> [item dicts]; sims carry an optional block_num used to nest them under a block.
    def _opt(v):
        return None if (v is None or (isinstance(v, float) and pd.isna(v))) else v

    def _sim_item(r) -> Dict[str, Any]:
        bn = _opt(getattr(r, "block_num", None))
        pr = _num(getattr(r, "pass_rate", None))
        cid = str(getattr(r, "challenge_id", "") or "")
        sid = _opt(getattr(r, "sim_id", None))
        return {"challenge_id": cid, "label": str(getattr(r, "label", "") or cid),
                "sim_id": None if sid is None else str(sid),
                "block_num": None if bn is None else int(bn),
                "sessions": int(_num(getattr(r, "sessions", 0)) or 0),
                "pass_rate": None if pr is None else round(pr, 3),
                "last": None if _opt(getattr(r, "last_period", None)) is None else str(r.last_period)}

    def _cbt_item(r) -> Dict[str, Any]:
        pr = _num(getattr(r, "pass_rate", None))
        cid = str(getattr(r, "courseid", "") or "")
        bn = _opt(getattr(r, "block_num", None))
        return {"courseid": cid, "coursename": str(getattr(r, "coursename", "") or cid),
                "block_num": None if bn is None else int(bn),
                "sessions": int(_num(getattr(r, "sessions", 0)) or 0),
                "pass_rate": None if pr is None else round(pr, 3),
                "last": None if _opt(getattr(r, "last_period", None)) is None else str(r.last_period)}

    def _grouped(df: Optional[pd.DataFrame], builder) -> Dict[str, List[Dict[str, Any]]]:
        out: Dict[str, List[Dict[str, Any]]] = {}
        if df is None or df.empty or "agent_id" not in df.columns:
            return out
        d = df.copy()
        d["agent_id"] = d["agent_id"].astype(str)
        for aid, grp in d.groupby("agent_id"):
            items = [builder(r) for r in grp.itertuples()]
            items.sort(key=lambda x: (-(x["sessions"] or 0), x["label"] if "label" in x else x.get("coursename", "")))
            out[str(aid)] = items
        return out

    sims_by_agent = _grouped(sims_taken, _sim_item)
    cbts_by_agent = _grouped(cbts_taken, _cbt_item)

    records: List[Dict[str, Any]] = []

    for aid in [str(x) for x in adf.index]:
        g = per_agent.get(aid)
        vals = ({str(r.metric): (_num(r.value), _num(r.benchmark)) for r in g.itertuples()}
                if g is not None else {})
        agent_sims = sims_by_agent.get(aid, [])
        agent_cbts = cbts_by_agent.get(aid, [])

        class_id = str(aget(aid, "class_id", "unassigned") or "unassigned")
        trainer = str(aget(aid, "trainer", "") or "")
        icp = str(aget(aid, "icp_client", "") or "")
        name = str(aget(aid, "agent_name", aid) or aid)

        # days since training start
        dss: Optional[int] = None
        start = aget(aid, "training_start_date")
        if start is not None:
            try:
                dss = int((rdate - pd.Timestamp(start).normalize()).days)
            except Exception:  # noqa: BLE001
                dss = None

        # Expected block by schedule -- used ONLY to judge on-track vs behind, never to
        # infer actual completion.
        expected_num: Optional[int] = None
        if dss is not None:
            due = [b.order for b in program.blocks
                   if b.expected_completion_day is not None and b.expected_completion_day <= dss]
            expected_num = max(due) if due else 0

        # ACTUAL current block comes from a progress feed (roster current_block_order) when
        # present; otherwise it is unknown -- we do NOT infer it from the expected schedule.
        cbo = aget(aid, "current_block_order")
        has_progress = cbo is not None
        current_num: Optional[int] = int(cbo) if has_progress else None

        # Skill ceiling: cap which blocks may surface a skill, so a "future" skill doesn't show on
        # a block the expert hasn't reached. With a progress feed -> the fed current block; without
        # one -> the furthest block they have crosswalked sim/CBT activity in (evidence of reaching);
        # None -> no evidence, so unbounded (skills surface under their curriculum block as before).
        act_blocks = [x["block_num"] for x in (agent_sims + agent_cbts) if x["block_num"] is not None]
        skill_ceiling: Optional[int] = current_num if has_progress else (max(act_blocks) if act_blocks else None)

        # pace = ACTUAL current block vs EXPECTED block (only when actual progress is known)
        if current_num is not None and expected_num is not None:
            on_track: Optional[bool] = current_num >= expected_num
            pace: Optional[str] = ("ahead" if current_num > expected_num
                                   else "on_track" if current_num == expected_num else "behind")
        else:
            on_track, pace = None, None

        block_defic: Dict[int, List[str]] = {}
        surfaced: set = set()
        blocks: List[Dict[str, Any]] = []
        for b in program.blocks:
            # "reached" drives block STATUS (feed-driven, or "not_tracked" without a feed).
            # SKILLS use `skill_reached` (the ceiling) instead: a skill is only surfaced under a
            # block at/under the ceiling, so a skill whose curriculum home is still ahead of the
            # expert is ignored until they reach that block. A block with no surfaced skills stays
            # BLANK -- we never roll a skill up under a block the expert hasn't reached.
            reached = (not has_progress) or (current_num is not None and b.order <= current_num)
            skill_reached = (skill_ceiling is None) or (b.order <= skill_ceiling)
            bskills = []
            if skill_reached:
                for sid in b.develops_all:
                    if sid not in vals:
                        continue
                    v, bm = vals[sid]
                    below = v is not None and bm is not None and v < bm
                    m = skill_meta.get(sid, {})
                    bskills.append({"skill": sid, "label": m.get("label", _title(sid)),
                                    "category": m.get("category", ""),
                                    "value": None if v is None else round(v, 3), "below": bool(below)})
                    surfaced.add(sid)
            has_below = any(s["below"] for s in bskills)
            if has_below:
                block_defic[b.order] = [s["skill"] for s in bskills if s["below"]]
            # Block status: ACTUAL-completion-driven when a progress feed exists; otherwise
            # data-driven. Never schedule-inferred -- pre-feed we do NOT claim blocks are passed.
            if has_progress:
                if b.order > current_num:
                    status = "locked"
                elif has_below:
                    status = "retraining"          # reached block with a below-mark skill
                elif b.order == current_num:
                    status = "in_progress"
                else:
                    status = "passed"
            else:
                status = "retraining" if has_below else "not_tracked"
            blocks.append({"num": b.order, "id": b.id, "name": b.label,
                           "short": short_desc(b.label), "status": status, "skills": bskills,
                           "sims": [s for s in agent_sims if s["block_num"] == b.order],
                           "cbts": [c for c in agent_cbts if c["block_num"] == b.order]})

        # Readiness + remediation consider only skills that surfaced under a reached block;
        # future-block skills (above the ceiling) are ignored until the expert reaches them.
        vv = [vals[sid][0] for sid in surfaced if vals[sid][0] is not None]
        avg_skill = round(sum(vv) / len(vv), 3) if vv else None
        deficient = [sid for sid in surfaced
                     if vals[sid][0] is not None and vals[sid][1] is not None and vals[sid][0] < vals[sid][1]]

        # Remediation targets blocks the expert has reached (a below-mark skill means they
        # attempted it). block_defic holds those; drive triggering from it. (Once real gate
        # test-call data exists, triggering is gate-driven and this fallback is unused.)
        id_by_order = {b.order: b.id for b in program.blocks}
        remediation = None
        if block_defic:
            triggered_ids = [id_by_order[o] for o in sorted(block_defic)]
            plan = plan_remediation(aid, program, policy, deficient_skills=deficient, triggered_blocks=triggered_ids)
            trig_nums = sorted(block_defic)
            prim_order = current_num if current_num in block_defic else max(block_defic)
            prim = next((b for b in program.blocks if b.order == prim_order), None)
            if prim is not None:
                focus_ids = block_defic.get(prim.order) or deficient
                focus_labels = [skill_meta.get(s, {}).get("label", _title(s)) for s in focus_ids]
                remediation = {
                    "reason": remediation_reason(short_desc(prim.label), focus_labels,
                                                 pass_mark=pass_mark, block_num=prim.order),
                    "focus_skills": focus_labels,
                    "triggered_blocks": trig_nums,
                    "primary_block": prim.order,
                    "primary_block_label": f"Block {prim.order} — {short_desc(prim.label)}",
                    "targets": [{"block": program.block_order(t.block_id), "kind": t.kind, "ref": t.ref,
                                 "reason_skills": [skill_meta.get(s, {}).get("label", _title(s)) for s in t.reason_skills]}
                                for t in plan.targets],
                    "groups": build_action_groups(f"Block {prim.order}", focus_labels),
                }

        if not vals:
            # No skill-readiness signal yet. A progress feed means they've started (awaiting
            # skill data) -> "in_training"; no feed and no data -> genuinely "not_started".
            status = "in_training" if has_progress else "not_started"
        elif block_defic:
            status = "retraining"                         # has a below-mark skill -> needs remediation
        elif has_progress and current_num is not None and current_num >= last_num:
            status = "completed"
        elif has_progress and on_track is False:
            status = "behind"
        elif has_progress:
            status = "on_track"
        else:
            status = "in_training"                        # has data, no deficiency, actual progress not tracked yet

        cur_block = next(({"num": bm["num"], "name": bm["name"], "short": bm["short"]}
                          for bm in blocks_meta if bm["num"] == current_num),
                         {"num": current_num, "name": "—", "short": "—"})

        # Completion hand-off: when an expert has cleared all blocks, summarize readiness
        # for the coach they're handed off to (in place of a remediation plan).
        handoff = None
        if status == "completed":
            cur_block = {"num": None, "name": "Completed", "short": "Completed", "done": True}
            n_pass = sum(1 for (v, bm) in vals.values() if v is not None and bm is not None and v >= bm)
            ranked = sorted(((v, skill_meta.get(m, {}).get("label", _title(m)))
                             for m, (v, bm) in vals.items() if v is not None), key=lambda x: x[0])
            handoff = {
                "ready": True,
                "summary": f"Completed all {last_num} learning blocks; {n_pass} of {len(vals)} "
                           f"tracked skills at or above the {round(pass_mark * 100)}% mark.",
                "watch": [lbl for _v, lbl in ranked[:3]],   # lowest (still-passing) skills to keep an eye on
                "note": "Ready for coach hand-off. Monitor the first live calls, especially the watch-list skills.",
            }

        block_orders = {b.order for b in program.blocks}
        sims_unmapped = [s for s in agent_sims if s["block_num"] not in block_orders]

        records.append({
            "id": aid, "name": name, "class_id": class_id, "trainer": trainer, "icp": icp,
            "days_since_start": dss, "status": status, "current_block": cur_block,
            "expected_block_num": expected_num, "on_track": on_track, "pace": pace, "avg_skill": avg_skill,
            "blocks": blocks, "remediation": remediation, "handoff": handoff,
            "hist": chmap.get(aid, []),
            "sims_unmapped": sims_unmapped,
        })

    records.sort(key=lambda r: (r["class_id"], r["name"]))
    meta = {
        "schema_version": SCHEMA_VERSION,
        "program": program.name,
        "pass_mark": pass_mark,
        "generated": str(report_date),
        "report_date": str(report_date),
        "blocks": blocks_meta,
        "roster_fields_synthetic": False,   # real roster by default; demo generator overrides to True
        "current_block_source": ("progress feed (actual block completion)" if any_progress
                                 else "not tracked yet — no progress feed; the expected date is used only to judge on-track"),
    }
    return records, meta


def build_export(records: List[Dict[str, Any]], meta: Dict[str, Any]) -> Dict[str, Any]:
    return {**meta, "experts": records}


@lru_cache(maxsize=None)
def _asset_data_uri(name: str, mime: str) -> str:
    """A bundled asset as a self-contained data URI so shared/emailed dashboards keep their branding."""
    raw = (Path(__file__).parent / "assets" / name).read_bytes()
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def render_html(records: List[Dict[str, Any]], meta: Dict[str, Any]) -> str:
    data_json = json.dumps(records, ensure_ascii=False, separators=(",", ":"))
    meta_json = json.dumps(meta, ensure_ascii=False)
    return (_HTML.replace("__DATA__", data_json).replace("__META__", meta_json)
            .replace("__LOGO__", _asset_data_uri("ascend_logo.svg", "image/svg+xml"))          # full wordmark (topbar)
            .replace("__LOGO_A__", _asset_data_uri("ascend_a_logo.png", "image/png")))          # "a" mark (modal)


def write_training_dashboard(out_dir: str | Path, records: List[Dict[str, Any]], meta: Dict[str, Any]) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    html_path = out / "training_dashboard.html"
    json_path = out / "training_dashboard.json"
    html_path.write_text(render_html(records, meta), encoding="utf-8")
    json_path.write_text(json.dumps(build_export(records, meta), ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Wrote %s and %s (%d experts)", html_path, json_path, len(records))
    return html_path


# --------------------------------------------------------------------------- #
# Self-contained HTML (palette/scaffold mirror the coaching expert dashboard)
# --------------------------------------------------------------------------- #
_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ASCEND Performance Decision Engine</title>
<style>
:root{
  --surface-1:#fcfcfb; --surface-2:#ffffff; --page:#f9f9f7;
  --text-1:#0b0b0b; --text-2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --border:rgba(11,11,11,.10);
  --series:#2a78d6; --track:#eef1f5;
  --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
  --warning-ink:#8a6100; --serious-ink:#a2432a;
  --shadow:0 10px 40px rgba(11,11,11,.18);
}
:root[data-theme="dark"]{
  --surface-1:#1a1a19; --surface-2:#201f1e; --page:#0d0d0d;
  --text-1:#fff; --text-2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --border:rgba(255,255,255,.10);
  --series:#3987e5; --track:#242422;
  --warning-ink:#fab219; --serious-ink:#ec835a;
  --shadow:0 10px 44px rgba(0,0,0,.55);
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --surface-1:#1a1a19; --surface-2:#201f1e; --page:#0d0d0d;
    --text-1:#fff; --text-2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --border:rgba(255,255,255,.10);
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
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:18px 0 6px}
.tile{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:14px 16px}
.tile .v{font-size:26px;font-weight:650;letter-spacing:-.01em}
.tile .k{color:var(--text-2);font-size:12px;margin-top:2px}
.tile.time{border-color:color-mix(in srgb,var(--series) 40%,var(--border))}
.filters{display:flex;gap:14px;align-items:flex-end;flex-wrap:wrap;margin:22px 0 4px}
.fgroup{display:flex;flex-direction:column;gap:5px}
.flabel{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:600}
.seg{display:inline-flex;background:var(--surface-1);border:1px solid var(--border);border-radius:9px;padding:3px;gap:2px;flex-wrap:wrap}
.seg button{border:0;background:transparent;color:var(--text-2);padding:6px 11px;border-radius:6px;
  font-size:12.5px;cursor:pointer;font-weight:600;white-space:nowrap}
.seg button:hover{color:var(--text-1)}
.seg button.on{background:var(--series);color:#fff}
select,input[type=search]{background:var(--surface-1);color:var(--text-1);border:1px solid var(--border);
  border-radius:8px;padding:7px 10px;font-size:13px;font-family:inherit;min-width:150px}
.count-note{color:var(--muted);font-size:12px;margin:14px 2px 0}
.grouphdr{display:flex;align-items:center;gap:10px;margin:26px 0 10px}
.grouphdr h2{font-size:14px;margin:0;letter-spacing:.01em}
.grouphdr .gc{color:var(--muted);font-size:12px}
.grouphdr .rule{flex:1;height:1px;background:var(--grid)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:12px}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:11px;padding:13px 14px;
  cursor:pointer;text-align:left;font:inherit;color:inherit;display:flex;flex-direction:column;gap:9px;
  transition:border-color .12s, transform .12s;width:100%}
.card:hover{border-color:var(--series);transform:translateY(-1px)}
.card:focus-visible{outline:2px solid var(--series);outline-offset:2px}
.card .row1{display:flex;align-items:center;justify-content:space-between;gap:8px}
.card .nm{font-weight:650;font-size:14.5px}
.card .dim{color:var(--text-2);font-size:12px}
.card .foot{display:flex;align-items:center;justify-content:space-between;gap:8px;color:var(--muted);font-size:11.5px}
.chip{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;font-weight:600;
  padding:2px 9px;border-radius:999px;border:1px solid var(--border);white-space:nowrap}
.chip .ico{font-size:10px;line-height:1}
.chip.good{color:var(--good)} .chip.warning{color:var(--warning-ink)}
.chip.serious{color:var(--serious-ink)} .chip.critical{color:var(--critical)}
.chip.good .ico{color:var(--good)} .chip.warning .ico{color:var(--warning)}
.chip.serious .ico{color:var(--serious)} .chip.critical .ico{color:var(--critical)}
.chip.muted{color:var(--muted)}
.dots{display:inline-flex;gap:3px;flex-wrap:wrap}
.dot{width:8px;height:8px;border-radius:999px;background:var(--track);border:1px solid var(--border)}
.dot.passed{background:var(--good);border-color:var(--good)}
.dot.in_progress{background:var(--series);border-color:var(--series)}
.dot.retraining{background:var(--serious);border-color:var(--serious)}
.overlay{position:fixed;inset:0;background:rgba(11,11,11,.5);backdrop-filter:blur(2px);
  display:none;align-items:flex-start;justify-content:center;padding:40px 16px;z-index:50;overflow:auto}
.overlay.open{display:flex}
.modal{background:var(--surface-1);border:1px solid var(--border);border-radius:16px;box-shadow:var(--shadow);
  width:100%;max-width:780px;overflow:hidden}
.mhead{padding:20px 22px 16px;border-bottom:1px solid var(--grid);position:relative}
.mhead .close{position:absolute;top:14px;right:14px;border:1px solid var(--border);background:var(--surface-2);
  color:var(--text-2);width:32px;height:32px;border-radius:8px;cursor:pointer;font-size:16px;line-height:1}
.mhead .close:hover{color:var(--text-1)}
.mhead .eyebrow{font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);font-weight:600}
.mhead h3{margin:3px 0 6px;font-size:20px}
.mhead .dims{display:flex;gap:8px;flex-wrap:wrap;align-items:center;color:var(--text-2);font-size:12.5px}
.mbody{padding:8px 22px 22px;max-height:72vh;overflow:auto}
.focusband{background:var(--surface-2);border:1px solid var(--border);border-radius:12px;
  padding:14px 16px;margin:16px 0 6px;display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap}
.focusband .k{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);font-weight:600}
.focusband .topic{font-size:18px;font-weight:650;margin:3px 0 4px}
.sec{margin-top:20px}
.sec > .h{display:flex;align-items:center;gap:8px;font-size:12px;text-transform:uppercase;
  letter-spacing:.04em;color:var(--muted);font-weight:700;margin-bottom:8px}
.lblk{border:1px solid var(--border);border-radius:10px;background:var(--surface-2);margin-bottom:8px;overflow:hidden}
.lblk .bhead{display:flex;align-items:center;gap:10px;padding:9px 12px;background:var(--surface-1)}
.lblk .bnum{font-variant-numeric:tabular-nums;color:var(--muted);font-weight:600;min-width:52px}
.lblk .bname{font-weight:600;flex:1}
.lblk .subh{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);font-weight:700;padding:8px 12px 0}
.cmtable td.sid{font-variant-numeric:tabular-nums;color:var(--text-2);white-space:nowrap}
.cmtable td .meta{color:var(--muted);font-weight:400;margin-left:6px}
.cmtable{width:100%;border-collapse:collapse;font-size:12.5px}
.cmtable td{border-top:1px solid var(--grid);padding:5px 12px;vertical-align:top}
.cmtable td.num{font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
.cmtable td.cat{color:var(--muted)}
.cmtable th{text-align:left;font-weight:600;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.03em;padding:0 12px 6px 0;border-bottom:1px solid var(--grid)}
.cmtable td.m{font-weight:650;color:var(--text-1)}
.retrain{display:flex;flex-direction:column;padding:12px 14px;border-radius:10px;
  border:1px solid color-mix(in srgb,var(--serious) 40%,var(--border));
  background:color-mix(in srgb,var(--serious) 8%,var(--surface-2));font-size:13px}
.retrain .rt-top{display:flex;gap:10px;align-items:flex-start}
.retrain .rt-ico{color:var(--serious);font-size:15px;line-height:1.2}
.retrain .backto{font-weight:700;font-size:14px;margin-bottom:3px}
.retrain .also{color:var(--muted);font-size:12px;margin-top:3px}
.actsgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:12px}
@media(max-width:640px){.actsgrid{grid-template-columns:1fr}}
.actcol{background:var(--surface-1);border:1px solid var(--border);border-radius:9px;padding:10px 12px;border-top-width:3px}
.actcol.learning{border-top-color:var(--series)}
.actcol.training_support{border-top-color:var(--good)}
.actcol.coaching{border-top-color:var(--warning)}
.acth{font-size:11.5px;text-transform:uppercase;letter-spacing:.04em;font-weight:700;color:var(--text-1);
  margin-bottom:2px}
.acth .actor{display:block;font-size:10px;font-weight:600;color:var(--muted);text-transform:none;letter-spacing:0;margin-top:1px}
.acts{margin:6px 0 0;padding-left:16px}
.acts li{margin:3px 0;font-size:12.5px;color:var(--text-2)}
.tag{display:inline-block;font-size:11.5px;padding:2px 8px;border-radius:5px;border:1px solid var(--border);
  color:var(--text-2);margin:3px 5px 0 0}
.okline{display:flex;gap:9px;align-items:center;padding:11px 13px;border-radius:10px;font-size:13px;
  border:1px solid color-mix(in srgb,var(--good) 35%,var(--border));
  background:color-mix(in srgb,var(--good) 7%,var(--surface-2));color:var(--text-2)}
.okline .ok-ico{color:var(--good)}
.handoff{padding:12px 14px;border-radius:10px;font-size:13px;
  border:1px solid color-mix(in srgb,var(--good) 40%,var(--border));
  background:color-mix(in srgb,var(--good) 8%,var(--surface-2))}
.handoff .hd-ico{color:var(--good);font-size:15px;line-height:1.2}
.mtools{position:absolute;top:12px;right:14px;display:flex;gap:6px;align-items:center;z-index:2}
.tbtn{border:1px solid var(--border);background:var(--surface-2);color:var(--text-2);border-radius:7px;
  padding:6px 9px;font-size:12px;cursor:pointer;font-weight:600;white-space:nowrap}
.tbtn:hover{color:var(--text-1)}
.mtools .close{position:static;width:auto;height:32px;padding:6px 9px}
.titlewrap{display:flex;align-items:center;gap:14px}
.mhead-l{display:flex;align-items:center;gap:14px}
.brand{height:50px;width:auto;display:block;flex:0 0 auto}
.brand-modal{height:50px;width:auto;display:block;flex:0 0 auto}
.foot-note{color:var(--muted);font-size:12px;margin-top:40px;border-top:1px solid var(--grid);padding-top:14px}
@media print{
  .wrap{display:none!important}
  .overlay{position:static!important;display:block!important;background:none!important;padding:0!important;overflow:visible!important}
  .modal{box-shadow:none!important;max-width:100%!important;border:0!important}
  .mbody{max-height:none!important;overflow:visible!important}
  .mtools{display:none!important}   /* drop the controls on the printed/PDF page; the ASCEND logo stays (upper-left) */
}
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div class="titlewrap">
      <img class="brand" src="__LOGO__" alt="ASCEND" />
      <div><h1 id="title"></h1><p class="sub" id="subline"></p></div>
    </div>
    <div class="right">
      <span class="exbadge" id="synbadge" style="display:none">● SYNTHETIC ROSTER FIELDS</span>
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
<script id="data" type="application/json">__DATA__</script>
<script id="meta" type="application/json">__META__</script>
<script>
"use strict";
const EXPERTS = JSON.parse(document.getElementById("data").textContent);
const META = JSON.parse(document.getElementById("meta").textContent);
const BENCH = META.pass_mark;
const byId = new Map(EXPERTS.map(e => [e.id, e]));
const SEV_ICON = {good:"✓", warning:"⚠", serious:"▲", critical:"✕", muted:"–"};
const STATUS_META = {
  not_started:{cls:"muted",label:"Not started"}, in_training:{cls:"muted",label:"In training"},
  on_track:{cls:"good",label:"On track"}, behind:{cls:"warning",label:"Behind schedule"},
  retraining:{cls:"serious",label:"Re-training needed"}, completed:{cls:"good",label:"Completed"},
  unknown:{cls:"muted",label:"Unknown"},
};
const BLK_META = {passed:{cls:"good",label:"Passed"}, in_progress:{cls:"muted",label:"In progress"},
  retraining:{cls:"serious",label:"Re-training"}, locked:{cls:"muted",label:"Locked"},
  not_tracked:{cls:"muted",label:"Not tracked"}};
function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
function pct(v){return (v==null)?"—":Math.round(v*100)+"%";}
function chip(cls,label){return `<span class="chip ${cls}"><span class="ico">${SEV_ICON[cls]||""}</span>${esc(label)}</span>`;}
function statusChip(s){const m=STATUS_META[s]||{cls:"muted",label:s};return chip(m.cls,m.label);}
function onTrackChip(e){
  if(e.pace==null) return chip("muted","pace: pending");
  if(e.pace==="ahead") return chip("good","ahead of pace");
  if(e.pace==="on_track") return chip("good","on track");
  return chip("warning","behind");
}
function curBlockText(e){const b=e.current_block||{};if(b.done)return "Completed — all blocks passed";return b.num!=null?("Block "+b.num+(b.short&&b.short!=="—"?" — "+b.short:"")):"Not tracked yet";}
function expectedText(e){return e.expected_block_num!=null?("Expected: Block "+e.expected_block_num+(e.days_since_start!=null?" · day "+e.days_since_start:"")):"";}

const state = {class_id:"__all", trainer:"__all", status:"__all", q:""};
const uniq = k => [...new Set(EXPERTS.map(e=>e[k]).filter(x=>x!=null&&x!==""))].sort();
function match(e){
  if(state.class_id!=="__all" && e.class_id!==state.class_id) return false;
  if(state.trainer!=="__all" && e.trainer!==state.trainer) return false;
  if(state.status!=="__all" && e.status!==state.status) return false;
  if(state.q && !e.name.toLowerCase().includes(state.q.toLowerCase())) return false;
  return true;
}

/* ---- tiles: recomputed from the FILTERED set (dynamic) ---- */
function renderTiles(rows){
  const n = rows.length;
  const c = s => rows.filter(e=>e.status===s).length;
  const scored = rows.filter(e=>e.avg_skill!=null);
  const avg = scored.length ? scored.reduce((a,e)=>a+e.avg_skill,0)/scored.length : null;
  const withDays = rows.filter(e=>e.days_since_start!=null);
  const avgDays = withDays.length ? Math.round(withDays.reduce((a,e)=>a+e.days_since_start,0)/withDays.length) : null;
  const paceKnown = rows.filter(e=>e.on_track!=null);
  const onPct = paceKnown.length ? Math.round(100*paceKnown.filter(e=>e.on_track).length/paceKnown.length) : null;
  const tiles = [
    {v:n, k:"Experts (filtered)"},
    {v:c("not_started"), k:"Not started"},
    {v:c("on_track"), k:"On track"},
    {v:c("behind"), k:"Behind schedule"},
    {v:c("retraining"), k:"Re-training needed"},
    {v:c("completed"), k:"Completed"},
    {v:pct(avg), k:"Avg skill readiness"},
    {v:(avgDays==null?"—":avgDays+"d"), k:"Avg days in program", time:true},
    {v:(onPct==null?"—":onPct+"%"), k:"On pace vs schedule", time:true},
  ];
  document.getElementById("tiles").innerHTML = tiles.map(t=>
    `<div class="tile ${t.time?"time":""}"><div class="v">${esc(t.v)}</div><div class="k">${esc(t.k)}</div></div>`).join("");
}

/* ---- filters ---- */
function sel(id,label,key){
  const opts = [`<option value="__all">All ${label}</option>`].concat(
    uniq(key).map(v=>`<option value="${esc(v)}" ${v===state[key]?"selected":""}>${esc(v)}</option>`));
  return `<div class="fgroup"><span class="flabel">${esc(label)}</span><select id="${id}">${opts.join("")}</select></div>`;
}
function buildFilters(){
  const stBtns = [["__all","All"],["not_started","Not started"],["on_track","On track"],["behind","Behind"],["retraining","Re-training"],["completed","Completed"]]
    .map(([v,l])=>`<button data-k="status" data-v="${v}" class="${v===state.status?"on":""}">${l}</button>`).join("");
  document.getElementById("filters").innerHTML =
    sel("clssel","class IDs","class_id")+
    sel("trsel","trainers","trainer")+
    `<div class="fgroup"><span class="flabel">Status</span><div class="seg">${stBtns}</div></div>`+
    `<div class="fgroup"><span class="flabel">Search</span><input type="search" id="q" placeholder="expert name…" value="${esc(state.q)}"></div>`;
  document.querySelectorAll('.seg button').forEach(b=>b.onclick=()=>{state[b.dataset.k]=b.dataset.v;buildFilters();render();});
  document.getElementById("clssel").onchange=e=>{state.class_id=e.target.value;render();};
  document.getElementById("trsel").onchange=e=>{state.trainer=e.target.value;render();};
  const q=document.getElementById("q"); q.oninput=e=>{state.q=e.target.value;render();};
}

/* ---- cards ---- */
function ladderDots(e){
  return `<span class="dots">`+e.blocks.map(b=>`<span class="dot ${b.status}" title="Block ${b.num} ${esc(b.short)}: ${esc((BLK_META[b.status]||{}).label||b.status)}"></span>`).join("")+`</span>`;
}
function card(e){
  const passed = e.blocks.filter(b=>b.status==="passed").length;
  const tracked = e.current_block && e.current_block.num!=null;
  const footL = tracked ? (passed+"/"+e.blocks.length+" passed") : "progress not tracked";
  return `<button class="card" data-id="${esc(e.id)}">`+
    `<div class="row1"><span class="nm">${esc(e.name)}</span>${statusChip(e.status)}</div>`+
    `<div class="dim">${esc(e.class_id)}${e.trainer?" · "+esc(e.trainer):""}</div>`+
    `<div class="dim">${esc(curBlockText(e))} ${onTrackChip(e)}</div>`+
    `<div class="dim muted">${esc(expectedText(e))}</div>`+
    `<div class="foot">${ladderDots(e)}<span>${footL} · skill ${pct(e.avg_skill)}</span></div>`+
  `</button>`;
}
function render(){
  const rows = EXPERTS.filter(match);
  renderTiles(rows);
  const groups = {};
  rows.forEach(e=>{(groups[e.class_id]=groups[e.class_id]||[]).push(e);});
  const out = Object.keys(groups).sort().map(cid=>{
    const cards = groups[cid].sort((a,b)=>a.name.localeCompare(b.name)).map(card).join("");
    return `<div class="grouphdr"><h2>Class ${esc(cid)}</h2><span class="gc">${groups[cid].length}</span><span class="rule"></span></div><div class="grid">${cards}</div>`;
  }).join("");
  document.getElementById("results").innerHTML = out || `<p class="muted">No experts match these filters.</p>`;
  document.getElementById("count").textContent = `${rows.length} of ${EXPERTS.length} experts`;
  document.querySelectorAll(".card").forEach(c=>c.onclick=()=>openModal(c.dataset.id));
}

/* ---- modal: learning blocks with skills rolled up under them ---- */
function skillRows(b){
  if(!b.skills || !b.skills.length) return `<tr><td class="cat" colspan="3">no tracked skills yet</td></tr>`;
  return b.skills.map(s=>`<tr><td>${esc(s.label)}</td><td class="cat">${esc(s.category)}</td>`+
    `<td class="num">${pct(s.value)} ${s.below?chip("critical","below"):chip("good","pass")}</td></tr>`).join("");
}
/* Recent coaching history — same block as the coaching expert dashboard (newest first). */
function coachingHistoryTable(rows){
  const body = rows.map(h=>`<tr>`+
    `<td><span class="tag">${esc(h.ty||"—")}</span></td>`+
    `<td class="m">${esc(h.tp||"—")}</td>`+
    `<td>${esc(h.dt||"—")}</td>`+
    `<td><span class="tag">${esc(h.st||"—")}</span></td>`+
  `</tr>`).join("");
  return `<table class="cmtable"><thead><tr>`+
    `<th>Type</th><th>Topic</th><th>Date</th><th>Status</th>`+
    `</tr></thead><tbody>${body}</tbody></table>`;
}
/* Hidden entirely when the expert has no recorded coaching history. */
function histSection(e){
  if(!(e.hist && e.hist.length)) return "";
  return `<div class="sec"><div class="h">Recent coaching history</div>${coachingHistoryTable(e.hist)}</div>`;
}
/* one row per sim practiced: label, ASC-SIM id (or —), pass-rate + sessions/last */
function simRow(s){
  const sid = s.sim_id ? esc(s.sim_id) : "—";
  const meta = `${s.sessions||0} sess${s.last?" · "+esc(s.last):""}`;
  return `<tr><td>${esc(s.label)}</td><td class="sid">${sid}</td>`+
    `<td class="num">${pct(s.pass_rate)}<span class="meta">${meta}</span></td></tr>`;
}
function simsTable(items){
  return `<table class="cmtable"><tbody>${items.map(simRow).join("")}</tbody></table>`;
}
function blocksView(e){
  return e.blocks.map(b=>{
    const m = BLK_META[b.status]||{cls:"muted",label:b.status};
    const head = `<div class="bhead"><span class="bnum">Block ${b.num}</span>`+
      `<span class="bname">${esc(b.short)}</span>${chip(m.cls,m.label)}</div>`;
    const hasSkills = b.skills && b.skills.length;
    const hasSims = b.sims && b.sims.length;
    const hasCbts = b.cbts && b.cbts.length;
    // Locked / not-yet-reached blocks stay blank -- no skill/sim/cbt content underneath.
    const blank = (b.status==="locked" || b.status==="not_tracked") && !hasSkills && !hasSims && !hasCbts;
    let body = "";
    if(!blank){
      body += `<table class="cmtable"><tbody>${skillRows(b)}</tbody></table>`;
      if(hasSims) body += `<div class="subh">Sims practiced</div>${simsTable(b.sims)}`;
      if(hasCbts) body += `<div class="subh">CBTs completed</div>${cbtTable(b.cbts)}`;
    }
    return `<div class="lblk${blank?" locked":""}">${head}${body}</div>`;
  }).join("");
}
/* one row per CBT completed under a block: coursename, pass-rate + sessions/last */
function cbtTable(items){
  return `<table class="cmtable"><tbody>${items.map(c=>
    `<tr><td>${esc(c.coursename)}</td><td class="num">${pct(c.pass_rate)}`+
    `<span class="meta">${c.sessions||0} sess${c.last?" · "+esc(c.last):""}</span></td></tr>`).join("")}</tbody></table>`;
}
/* Sims the expert practiced that don't yet resolve to a block (no sim_id crosswalk). */
function simsUnmappedSection(e){
  if(!(e.sims_unmapped && e.sims_unmapped.length)) return "";
  return `<div class="sec"><div class="h">Other sims practiced (not yet mapped to a block)</div>`+
    `${simsTable(e.sims_unmapped)}</div>`;
}
function actCol(cls,g){
  if(!g) return "";
  return `<div class="actcol ${cls}"><div class="acth">${esc(g.label)}<span class="actor">${esc(g.actor)}</span></div>`+
    `<ul class="acts">`+(g.actions||[]).map(a=>`<li>${esc(a)}</li>`).join("")+`</ul></div>`;
}
function remediationView(e){
  const r = e.remediation;
  if(!r) return `<div class="okline"><span class="ok-ico">${SEV_ICON.good}</span>On track — no remediation required.</div>`;
  const tags = (r.focus_skills||[]).map(s=>`<span class="tag">${esc(s)}</span>`).join("");
  const g = r.groups||{};
  const backto = r.primary_block_label || ("Block "+r.primary_block);
  const others = (r.triggered_blocks||[]).filter(b=>b!==r.primary_block);
  const alsoLine = others.length ? `<div class="also">Also flagged: ${others.map(b=>"Block "+b).join(", ")}</div>` : "";
  return `<div class="retrain"><div class="rt-top"><span class="rt-ico">${SEV_ICON.serious}</span>`+
    `<div><div class="backto">↩ Go back to ${esc(backto)}</div><b>${esc(r.reason)}</b>`+
    `<div style="margin-top:4px">Focus behaviors: ${tags}</div>${alsoLine}</div></div>`+
    `<div class="actsgrid">${actCol("learning",g.learning)}${actCol("training_support",g.training_support)}${actCol("coaching",g.coaching)}</div>`+
  `</div>`;
}
function handoffView(e){
  const h=e.handoff; if(!h) return "";
  const tags=(h.watch||[]).map(s=>`<span class="tag">${esc(s)}</span>`).join("");
  return `<div class="handoff"><div style="display:flex;gap:10px;align-items:flex-start"><span class="hd-ico">${SEV_ICON.good}</span><div>`+
    `<b>Completed Launchpad — ready for coach hand-off.</b>`+
    `<div style="margin-top:4px">${esc(h.summary)}</div>`+
    (tags?`<div style="margin-top:4px">Watch in production: ${tags}</div>`:"")+
    `<div class="muted" style="margin-top:6px">${esc(h.note)}</div>`+
  `</div></div></div>`;
}
/* plain-text full summary a trainer can copy / email to a coach */
function summaryText(e){
  const L=[];
  L.push("TRAINING SUMMARY — "+e.name+" (#"+e.id+")");
  L.push("Class "+e.class_id+(e.trainer?" · trainer "+e.trainer:""));
  L.push("Status: "+((STATUS_META[e.status]||{}).label||e.status)+(e.pace?"  ·  pace: "+e.pace:""));
  L.push(curBlockText(e)+(e.expected_block_num!=null?"  ·  "+expectedText(e):""));
  L.push("Skill readiness: "+pct(e.avg_skill));
  if(e.handoff){L.push(""); L.push("COMPLETION HAND-OFF"); L.push("- "+e.handoff.summary);
    if((e.handoff.watch||[]).length) L.push("- Watch in production: "+e.handoff.watch.join(", ")); L.push("- "+e.handoff.note);}
  else if(e.remediation){const r=e.remediation; L.push(""); L.push("REMEDIATION — "+(r.primary_block_label||("Block "+r.primary_block)));
    L.push("- "+r.reason); L.push("- Focus behaviors: "+(r.focus_skills||[]).join(", "));
    ["learning","training_support","coaching"].forEach(k=>{const g=(r.groups||{})[k]; if(g){L.push("  "+g.label+" ("+g.actor+"):");(g.actions||[]).forEach(a=>L.push("    • "+a));}});}
  L.push(""); L.push("LEARNING BLOCKS");
  e.blocks.forEach(b=>{const st=(BLK_META[b.status]||{}).label||b.status;
    const below=(b.skills||[]).filter(s=>s.below).map(s=>s.label);
    L.push("- Block "+b.num+" "+b.short+" — "+st+(below.length?"  (below: "+below.join(", ")+")":""));
    (b.sims||[]).forEach(s=>L.push("    sim: "+s.label+(s.sim_id?" ["+s.sim_id+"]":"")+" — "+pct(s.pass_rate)+", "+(s.sessions||0)+" sess"));
    (b.cbts||[]).forEach(c=>L.push("    cbt: "+c.coursename+" — "+pct(c.pass_rate)+", "+(c.sessions||0)+" sess"));});
  const other=e.sims_unmapped||[];
  if(other.length){L.push(""); L.push("OTHER SIMS PRACTICED (unmapped)");
    other.forEach(s=>L.push("- "+s.label+" — "+pct(s.pass_rate)+", "+(s.sessions||0)+" sess"));}
  L.push(""); L.push("From the training dashboard"+(META.notice?" — SIMULATED review data":"")+".");
  return L.join("\n");
}
function openModal(id){
  const e = byId.get(id); if(!e) return;
  const exp = e.expected_block_num!=null ? ("expected Block "+e.expected_block_num) : "expected —";
  const primary = e.handoff ? "Completion hand-off" : "Remediation";
  const primaryView = e.handoff ? handoffView(e) : remediationView(e);
  document.getElementById("modal").innerHTML = `
    <div class="mhead">
      <div class="mtools">
        <button id="mCopy" class="tbtn" type="button" title="Copy summary to clipboard">⧉ Copy</button>
        <button id="mEmail" class="tbtn" type="button" title="Email summary to a coach">✉ Email</button>
        <button id="mPrint" class="tbtn" type="button" title="Print / Save as PDF">⎙ PDF</button>
        <button class="close" id="closeBtn" aria-label="Close">✕</button>
      </div>
      <div class="mhead-l">
        <img class="brand-modal" src="__LOGO_A__" alt="ASCEND" />
        <div>
          <div class="eyebrow">Training record</div>
          <h3 id="mTitle">${esc(e.name)}</h3>
          <div class="dims">${statusChip(e.status)}<span>#${esc(e.id)}</span><span>Class ${esc(e.class_id)}</span>`+
            `${e.trainer?`<span>trainer ${esc(e.trainer)}</span>`:""}${e.icp?`<span>${esc(e.icp)}</span>`:""}</div>
        </div>
      </div>
    </div>
    <div class="mbody">
      <div class="focusband">
        <div><div class="k">Current learning block</div><div class="topic">${esc(curBlockText(e))}</div>
          <div>${onTrackChip(e)} <span class="muted">${esc(exp)}${e.days_since_start!=null?` · day ${e.days_since_start}`:""}</span></div></div>
        <div><div class="k">Skill readiness</div><div class="topic">${pct(e.avg_skill)}</div></div>
      </div>
      <div class="sec"><div class="h">${primary}</div>${primaryView}</div>
      ${histSection(e)}
      <div class="sec"><div class="h">Learning blocks &amp; skills</div>${blocksView(e)}</div>
      ${simsUnmappedSection(e)}
    </div>`;
  const ov = document.getElementById("overlay"); ov.classList.add("open");
  document.getElementById("closeBtn").onclick = closeModal;
  document.getElementById("mCopy").onclick = ()=>{const b=document.getElementById("mCopy");
    (navigator.clipboard?navigator.clipboard.writeText(summaryText(e)):Promise.reject())
      .then(()=>{b.textContent="Copied ✓";setTimeout(()=>b.textContent="⧉ Copy",1500);})
      .catch(()=>{b.textContent="Copy failed";setTimeout(()=>b.textContent="⧉ Copy",1500);});};
  document.getElementById("mEmail").onclick = ()=>{
    location.href="mailto:?subject="+encodeURIComponent("Training summary: "+e.name+" ("+e.class_id+")")
      +"&body="+encodeURIComponent(summaryText(e).slice(0,1800));};
  document.getElementById("mPrint").onclick = ()=>window.print();
  document.getElementById("closeBtn").focus();
  if(location.hash !== "#e="+id) history.replaceState(null,"","#e="+id);
}
function closeModal(){document.getElementById("overlay").classList.remove("open");
  if(location.hash.startsWith("#e=")) history.replaceState(null,"","#");}
document.getElementById("overlay").addEventListener("click", e=>{ if(e.target.id==="overlay") closeModal(); });
document.addEventListener("keydown", e=>{ if(e.key==="Escape") closeModal(); });
(function(){const btn=document.getElementById("themebtn");btn.onclick=()=>{
  const cur=document.documentElement.getAttribute("data-theme");
  document.documentElement.setAttribute("data-theme", cur==="dark"?"light":"dark");};})();

document.getElementById("title").textContent = META.program + " — Training Dashboard";
document.getElementById("subline").textContent =
  `Pass mark ${Math.round(BENCH*100)}% · ${EXPERTS.length} experts · generated ${META.generated}`;
if(META.notice){const sb=document.getElementById("synbadge"); sb.textContent="● "+(META.notice_badge||"SIMULATED"); sb.style.display="";
  document.getElementById("foot").textContent = META.notice;}
else if(META.roster_fields_synthetic){document.getElementById("synbadge").style.display="";
  document.getElementById("foot").textContent = "Some roster fields (class ID, training start date, current block) are synthetic placeholders for validation — swap in the real roster to make pacing/on-track live.";}
else if(META.current_block_source && String(META.current_block_source).indexOf("not tracked")>=0){
  document.getElementById("foot").textContent = "Roster is the source of truth for who/class/trainer/start date. Progress is " + META.current_block_source + " — it becomes exact once a per-expert progress feed (block completions / test calls) is wired.";}
buildFilters(); render();
(function(){const m=/^#e=(.+)$/.exec(location.hash); if(m && byId.has(m[1])) openModal(m[1]);})();
</script>
</body>
</html>"""
