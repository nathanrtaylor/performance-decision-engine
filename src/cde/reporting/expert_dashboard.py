"""Interactive expert-coaching dashboard (self-contained HTML).

Groups experts (agents) by ``icp_client`` -> ``mascot`` and, on selecting an
expert, opens a modal showing the coaching focus, the three "why" narratives, and
full explainability (driver evidence + numbers, alternatives considered, and the
signals that were excluded).

The output is one self-contained HTML file (embedded JSON + inline CSS + vanilla
JS, no external assets) matching the codebase dashboard palette, light + dark.

This module holds the reusable core. The pipeline calls :func:`write_expert_dashboard`
with the in-memory receipts + agents frames; ``tools/expert_dashboard.py`` is a CLI
wrapper that can also rebuild receipts from a saved run's CSVs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd

__all__ = [
    "agents_map_from_df",
    "build_expert",
    "build_experts",
    "render_html",
    "write_expert_dashboard",
]


# --------------------------------------------------------------------------- #
# expert-record construction
# --------------------------------------------------------------------------- #
def agents_map_from_df(agents: Optional[pd.DataFrame]) -> Dict[str, Dict[str, str]]:
    """agent_id -> {icp, mascot, coach, name}, using the most recent week per agent.

    Accepts the normalized ``agents`` frame (period key ``week_ending`` or ``period``).
    ``agent_id`` is cast to ``str`` on both sides so numeric ids join reliably.
    """
    if agents is None or agents.empty or "agent_id" not in agents.columns:
        return {}
    df = agents.copy()
    df["agent_id"] = df["agent_id"].astype(str)
    period_col = "week_ending" if "week_ending" in df.columns else ("period" if "period" in df.columns else None)
    if period_col is not None:
        df = df.sort_values(period_col)
    df = df.drop_duplicates("agent_id", keep="last")

    def _s(v: Any, default: str = "") -> str:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return default
        return str(v)

    out: Dict[str, Dict[str, str]] = {}
    for _, r in df.iterrows():
        out[r["agent_id"]] = {
            "icp": _s(r.get("icp_client"), "(unknown)") or "(unknown)",
            "mascot": _s(r.get("mascot"), "(unknown)") or "(unknown)",
            "coach": _s(r.get("coach")),
            "name": _s(r.get("agent_name")),
        }
    return out


def _num(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        f = float(x)
        return None if pd.isna(f) else f
    except Exception:
        return None


def _driver(d: Dict[str, Any]) -> Dict[str, Any]:
    """Compact driver record; keeps only present numbers (short keys shrink the payload)."""
    out: Dict[str, Any] = {"m": d.get("metric")}
    for src, dst in (
        ("value", "v"), ("benchmark", "b"), ("gap", "g"),
        ("level_score", "ls"), ("trend_score", "ts"), ("risk_score", "rs"),
        ("confidence_score", "cs"), ("cohort_pct", "cp"),
        ("trend_8w", "t8"), ("recency_shift", "rc"),
    ):
        val = _num(d.get(src))
        if val is not None:
            out[dst] = val
    direction = d.get("direction")
    if direction is not None and not (isinstance(direction, float) and pd.isna(direction)):
        out["dir"] = str(direction)
    return out


def _as_dict(x: Any) -> Optional[Dict[str, Any]]:
    return x if isinstance(x, dict) else None


def _as_list(x: Any) -> List[Any]:
    return x if isinstance(x, list) else []


def _as_str_list(x: Any) -> List[str]:
    """Coerce a reasons field to a list of strings.

    In-pipeline it is already a list; when read back from excluded_signals.csv it arrives as a
    string (a Python-list repr like "['LOW_DENOMINATOR']" or a bare/comma-joined value).
    """
    if isinstance(x, list):
        return [str(v) for v in x]
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return []
    s = str(x).strip()
    if not s:
        return []
    if s.startswith("[") and s.endswith("]"):
        import ast
        try:
            v = ast.literal_eval(s)
            return [str(i) for i in v] if isinstance(v, (list, tuple)) else [str(v)]
        except Exception:
            pass
    return [p.strip() for p in s.split(",") if p.strip()]


def build_expert(r: Dict[str, Any], amap: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    aid = str(r.get("agent_id"))
    dim = amap.get(aid, {"icp": "(unknown)", "mascot": "(unknown)", "coach": "", "name": ""})
    nar = _as_dict(r.get("narrative")) or {}
    theme = _as_dict(r.get("theme_membership"))
    comp = []
    for c in _as_list(r.get("competing_topics")):
        comp.append({
            "topic": c.get("topic"),
            "metric": c.get("metric"),
            "reason": c.get("reason_not_selected"),
            "g": _num(c.get("gap")),
        })
    excl = []
    for e in _as_list(r.get("excluded_signals")):
        excl.append({
            "m": e.get("metric"),
            "reasons": _as_str_list(e.get("exclusion_reasons")),
            "v": _num(e.get("value")),
            "conf": _num(e.get("confidence")),
        })
    # Core-metrics block: new shape is {period, as_of_latest, metrics}; tolerate the legacy
    # bare-list shape (older decision_receipts.jsonl) which carries no period info.
    cm_raw = r.get("core_metrics")
    if isinstance(cm_raw, dict):
        cm_rows = _as_list(cm_raw.get("metrics"))
        cm_period = cm_raw.get("period")
        cm_stale = not bool(cm_raw.get("as_of_latest", True))
    else:
        cm_rows = _as_list(cm_raw)
        cm_period = None
        cm_stale = False

    prov = r.get("provenance") or {}
    return {
        "id": aid,
        "name": dim.get("name") or "",
        "icp": dim["icp"],
        "mascot": dim["mascot"],
        "coach": dim["coach"],
        "tier": r.get("tier"),
        "adv": bool(r.get("advisory")) if r.get("advisory") is not None else False,
        "topic": r.get("recommended_topic"),
        "conv": r.get("conversation_type"),
        "why": {
            "this": nar.get("why_this"),
            "now": nar.get("why_now"),
            "not": nar.get("why_not_others"),
        },
        "drivers": [_driver(d) for d in _as_list(r.get("drivers"))],
        "cm": [_driver(d) for d in cm_rows],
        "cmp": cm_period,   # week-ending date (YYYY-MM-DD) the core metrics are from
        "cms": cm_stale,    # True when that week is older than the recommendation week

        "theme": None if not theme else {
            "nd": theme.get("n_deficient"),
            "nm": theme.get("n_members"),
            "dm": theme.get("deficient_metrics") or [],
        },
        "comp": comp,
        "excl": excl,
        "prov": {
            "cv": prov.get("config_version"),
            "ds": prov.get("data_snapshot"),
            "ev": prov.get("engine_version"),
            "ch": r.get("config_hash"),
        },
    }


def build_experts(receipts: List[Dict[str, Any]], amap: Dict[str, Dict[str, str]]) -> List[Dict[str, Any]]:
    experts = [build_expert(r, amap) for r in receipts if r.get("tier") != "abstained"]
    # break-glass first (most urgent), then theme, then single; then icp, mascot, id
    tier_rank = {"break_glass": 0, "theme": 1, "single": 2}
    experts.sort(key=lambda e: (
        str(e["icp"]), str(e["mascot"]), tier_rank.get(e["tier"], 9), str(e["id"])
    ))
    return experts


# --------------------------------------------------------------------------- #
# HTML rendering
# --------------------------------------------------------------------------- #
def render_html(experts: List[Dict[str, Any]], meta: Dict[str, Any]) -> str:
    data_json = json.dumps(experts, ensure_ascii=False, separators=(",", ":"))
    meta_json = json.dumps(meta, ensure_ascii=False)
    return (
        HTML_TEMPLATE
        .replace("__DATA__", data_json)
        .replace("__META__", meta_json)
    )


def write_expert_dashboard(
    out_path: Union[str, Path],
    receipts: Union[pd.DataFrame, List[Dict[str, Any]]],
    agents: Optional[pd.DataFrame],
    meta: Dict[str, Any],
) -> Dict[str, int]:
    """Render and write the expert dashboard. Returns {'experts': n, 'matched': m}."""
    if isinstance(receipts, pd.DataFrame):
        records = receipts.to_dict(orient="records")
    else:
        records = list(receipts or [])
    amap = agents_map_from_df(agents)
    experts = build_experts(records, amap)
    Path(out_path).write_text(render_html(experts, meta), encoding="utf-8")
    return {
        "experts": len(experts),
        "matched": sum(1 for e in experts if e["icp"] != "(unknown)"),
    }


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Coaching Decision Receipts — Experts</title>
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
a{color:var(--series)}
h1{font-size:22px;margin:0 0 2px}
.sub{color:var(--text-2);font-size:12.5px;margin:0}
.muted{color:var(--muted)}
.topbar{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap}
.themebtn{background:var(--surface-1);color:var(--text-2);border:1px solid var(--border);
  border-radius:999px;padding:6px 13px;font-size:12.5px;cursor:pointer;font-weight:600}
.themebtn:hover{color:var(--text-1)}

/* tiles */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0 6px}
.tile{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:14px 16px}
.tile .v{font-size:28px;font-weight:650;letter-spacing:-.01em}
.tile .k{color:var(--text-2);font-size:12px;margin-top:2px}
.tile .spark{display:flex;gap:10px;margin-top:8px}
.tile .spark span{font-size:12px;color:var(--text-2);display:inline-flex;align-items:center;gap:5px}

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

/* expert grid */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(258px,1fr));gap:12px}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:11px;padding:13px 14px;
  cursor:pointer;text-align:left;font:inherit;color:inherit;display:flex;flex-direction:column;gap:9px;
  transition:border-color .12s, transform .12s}
.card:hover{border-color:var(--series);transform:translateY(-1px)}
.card:focus-visible{outline:2px solid var(--series);outline-offset:2px}
.card .row1{display:flex;align-items:center;justify-content:space-between;gap:8px}
.card .aid{font-weight:650;font-size:14.5px}
.card .dim{color:var(--text-2);font-size:12px}
.card .focus{font-size:13px;color:var(--text-1)}
.card .focus b{font-weight:650}
.card .conv{color:var(--muted);font-size:11.5px}

/* chips */
.chip{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;font-weight:600;
  padding:2px 9px;border-radius:999px;border:1px solid var(--border);white-space:nowrap}
.chip .ico{font-size:10px;line-height:1}
.chip.tier-break_glass{color:var(--critical);border-color:color-mix(in srgb,var(--critical) 40%,var(--border))}
.chip.tier-theme{color:var(--series);border-color:color-mix(in srgb,var(--series) 40%,var(--border))}
.chip.tier-single{color:var(--text-2)}
.chip.good{color:var(--good)} .chip.warning{color:var(--warning-ink)}
.chip.serious{color:var(--serious-ink)} .chip.critical{color:var(--critical)}
.chip.good .ico{color:var(--good)} .chip.warning .ico{color:var(--warning)}
.chip.serious .ico{color:var(--serious)} .chip.critical .ico{color:var(--critical)}

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
.focusband .conv{color:var(--text-2);font-size:13px}
.advbanner{display:flex;align-items:center;gap:10px;margin:10px 0 0;padding:10px 12px;
  border:1px solid color-mix(in srgb,var(--good) 35%,var(--border));border-radius:9px;
  background:color-mix(in srgb,var(--good) 7%,var(--surface-2));font-size:12.5px;color:var(--text-2)}

.sec{margin-top:20px}
.sec > .h{display:flex;align-items:center;gap:8px;font-size:12px;text-transform:uppercase;
  letter-spacing:.04em;color:var(--muted);font-weight:700;margin-bottom:8px}
.copybtn{margin-left:auto;flex:none;display:inline-flex;align-items:center;justify-content:center;
  width:25px;height:25px;border:1px solid var(--border);background:var(--surface-2);color:var(--text-2);
  border-radius:7px;cursor:pointer;padding:0;line-height:0}
.copybtn svg{width:14px;height:14px;display:block}
.copybtn:hover{color:var(--text-1)}
.copybtn.done{color:var(--good);border-color:color-mix(in srgb,var(--good) 45%,var(--border))}
/* core-metrics table (replaces bar meters for a copy-friendly view) */
.cmtable{width:100%;border-collapse:collapse;font-size:12.5px}
.cmtable th{text-align:left;font-weight:600;color:var(--muted);font-size:11px;text-transform:uppercase;
  letter-spacing:.03em;padding:0 12px 6px 0;border-bottom:1px solid var(--grid)}
.cmtable td{padding:8px 12px 8px 0;border-bottom:1px solid var(--grid);vertical-align:middle}
.cmtable tr:last-child td{border-bottom:none}
.cmtable td.m{font-weight:650;color:var(--text-1)}
.cmtable td.num{font-variant-numeric:tabular-nums;color:var(--text-1);font-weight:600}
.cmtable th.num,.cmtable td.num{text-align:right}
.cmnote{font-size:12px;color:var(--muted);margin:-2px 0 9px}
.cmnote.stale{color:var(--warning-ink);font-weight:600}
.why{background:var(--surface-2);border:1px solid var(--border);border-left:3px solid var(--series);
  border-radius:8px;padding:11px 13px;margin-bottom:9px}
.why .lab{font-size:11.5px;font-weight:700;color:var(--series);margin-bottom:3px;letter-spacing:.02em}
.why.now .lab{color:var(--serious-ink)} .why.now{border-left-color:var(--serious)}
.why.not .lab{color:var(--muted)} .why.not{border-left-color:var(--baseline)}
.why p{margin:0;font-size:13.5px;line-height:1.5}

/* driver evidence */
.drv{border:1px solid var(--border);border-radius:9px;padding:11px 12px;margin-bottom:9px;background:var(--surface-2)}
.drv .top{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px}
.drv .m{font-weight:650;font-size:13.5px}
.meter{position:relative;height:16px;background:var(--track);border-radius:5px;margin:9px 0 6px}
.meter .fill{position:absolute;left:0;top:0;height:16px;background:var(--series);border-radius:5px 4px 4px 5px;min-width:3px}
.meter .bench{position:absolute;top:-3px;width:2px;height:22px;background:var(--baseline)}
.drv .nums{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--text-2);font-variant-numeric:tabular-nums}
.drv .nums b{color:var(--text-1);font-weight:650}
.legend{display:flex;gap:14px;align-items:center;color:var(--muted);font-size:11px;margin:2px 2px 10px}
.legend .sw{display:inline-block;width:10px;height:10px;border-radius:3px;background:var(--series);vertical-align:middle;margin-right:4px}
.legend .bk{display:inline-block;width:2px;height:12px;background:var(--baseline);vertical-align:middle;margin-right:4px}

.altrow,.exrow{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;
  padding:8px 0;border-bottom:1px solid var(--grid);font-size:13px}
.altrow:last-child,.exrow:last-child{border-bottom:none}
.altrow .t{font-weight:600}
.altrow .r,.exrow .r{color:var(--text-2);font-size:12px}
.exrow .m{font-weight:600}
.tag{display:inline-block;font-size:10.5px;font-weight:600;color:var(--muted);border:1px solid var(--border);
  border-radius:5px;padding:1px 6px;margin:0 4px 4px 0;letter-spacing:.02em}
.empty{color:var(--muted);font-size:12.5px;font-style:italic;padding:2px 0}

.prov{margin-top:18px;padding-top:12px;border-top:1px solid var(--grid);color:var(--muted);
  font-size:11.5px;display:flex;gap:16px;flex-wrap:wrap;font-variant-numeric:tabular-nums}
.foot{color:var(--muted);font-size:11.5px;margin-top:26px}
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div>
      <h1>Coaching decision receipts</h1>
      <p class="sub" id="subline"></p>
    </div>
    <button class="themebtn" id="themeToggle" title="Toggle light / dark">◐ Theme</button>
  </div>

  <div class="tiles" id="tiles"></div>

  <div class="filters">
    <div class="fgroup">
      <span class="flabel">ICP client</span>
      <div class="seg" id="icpSeg"></div>
    </div>
    <div class="fgroup">
      <span class="flabel">Mascot</span>
      <select id="mascotSel"></select>
    </div>
    <div class="fgroup">
      <span class="flabel">Coaching type</span>
      <div class="seg" id="tierSeg"></div>
    </div>
    <div class="fgroup">
      <span class="flabel">Find expert</span>
      <input type="search" id="search" placeholder="Name or agent ID…">
    </div>
  </div>
  <div class="count-note" id="countNote"></div>

  <div id="results"></div>
  <p class="foot" id="foot"></p>
</div>

<div class="overlay" id="overlay" role="dialog" aria-modal="true" aria-labelledby="mTitle">
  <div class="modal" id="modal"></div>
</div>

<script id="experts" type="application/json">__DATA__</script>
<script id="meta" type="application/json">__META__</script>
<script>
"use strict";
const EXPERTS = JSON.parse(document.getElementById("experts").textContent);
const META = JSON.parse(document.getElementById("meta").textContent);
const RENDER_CAP = 600;

const TIER_LABEL = {break_glass:"Break-glass", theme:"Theme", single:"Single behavior"};
const TIER_ICON  = {break_glass:"✕", theme:"◆", single:"•"};
const SEV_ICON   = {critical:"✕", serious:"▲", warning:"⚠", good:"✓"};

const state = {icp:"all", mascot:"all", tier:"all", q:""};
const byId = new Map(EXPERTS.map(e => [e.id, e]));

function esc(s){ return String(s==null?"":s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function nameOf(e){ return (e.name && e.name.trim()) ? e.name : ("Agent " + e.id); }
function fmtNum(x){
  if(x===null||x===undefined||Number.isNaN(x)) return "—";
  const a=Math.abs(x);
  if(a!==0 && (a<0.001 || a>=100000)) return x.toExponential(2);
  if(a>=1000) return x.toLocaleString(undefined,{maximumFractionDigits:0});
  if(a>=1) return (+x).toFixed(2);
  return (+x).toFixed(3);
}

/* ---- tiles ---- */
function renderTiles(){
  const total = EXPERTS.length;
  const t = {break_glass:0, theme:0, single:0};
  EXPERTS.forEach(e => { if(t[e.tier]!==undefined) t[e.tier]++; });
  const el = document.getElementById("tiles");
  el.innerHTML = `
    <div class="tile"><div class="v">${total.toLocaleString()}</div><div class="k">Experts with a coaching focus</div></div>
    <div class="tile"><div class="v">${t.break_glass.toLocaleString()}</div><div class="k">Break-glass (critical)</div>
      <div class="spark"><span><span class="chip critical" style="padding:1px 7px"><span class="ico">✕</span></span> urgent single behavior</span></div></div>
    <div class="tile"><div class="v">${t.theme.toLocaleString()}</div><div class="k">Coaching themes</div>
      <div class="spark"><span>pattern across related behaviors</span></div></div>
    <div class="tile"><div class="v">${t.single.toLocaleString()}</div><div class="k">Single-behavior focus</div></div>`;
  document.getElementById("subline").textContent =
    `${META.run_id||""} · data snapshot ${META.data_snapshot||"?"} · engine ${META.engine_version||"?"} · generated ${META.generated||""}`;
  document.getElementById("foot").textContent =
    `Config ${META.config_version||"?"} (${META.config_hash||"?"}) · ${total.toLocaleString()} decision receipts · priority scores are internal and omitted from this view.`;
}

/* ---- filters ---- */
function uniq(arr){ return Array.from(new Set(arr)); }
function buildFilters(){
  const icps = uniq(EXPERTS.map(e=>e.icp)).sort();
  const icpSeg = document.getElementById("icpSeg");
  icpSeg.innerHTML = segBtn("all","All",state.icp==="all") +
    icps.map(v=>segBtn(v, v, state.icp===v)).join("");
  icpSeg.querySelectorAll("button").forEach(b=>b.onclick=()=>{ state.icp=b.dataset.v; state.mascot="all"; syncMascot(); render(); syncSeg(); });

  const tierSeg = document.getElementById("tierSeg");
  const tiers = ["break_glass","theme","single"];
  tierSeg.innerHTML = segBtn("all","All",state.tier==="all") +
    tiers.map(v=>segBtn(v, TIER_LABEL[v], state.tier===v)).join("");
  tierSeg.querySelectorAll("button").forEach(b=>b.onclick=()=>{ state.tier=b.dataset.v; render(); syncSeg(); });

  syncMascot();
  document.getElementById("mascotSel").onchange = e => { state.mascot=e.target.value; render(); };
  document.getElementById("search").oninput = e => { state.q=e.target.value.trim(); render(); };
}
function segBtn(v,label,on){ return `<button data-v="${esc(v)}" class="${on?"on":""}">${esc(label)}</button>`; }
function syncSeg(){
  document.querySelectorAll("#icpSeg button").forEach(b=>b.classList.toggle("on", b.dataset.v===state.icp));
  document.querySelectorAll("#tierSeg button").forEach(b=>b.classList.toggle("on", b.dataset.v===state.tier));
}
function syncMascot(){
  const pool = EXPERTS.filter(e=> state.icp==="all"||e.icp===state.icp);
  const mascots = uniq(pool.map(e=>e.mascot)).sort();
  const sel = document.getElementById("mascotSel");
  sel.innerHTML = `<option value="all">All mascots (${mascots.length})</option>` +
    mascots.map(m=>`<option value="${esc(m)}">${esc(m)}</option>`).join("");
  if(!mascots.includes(state.mascot)) state.mascot="all";
  sel.value = state.mascot;
}

/* ---- results ---- */
function filtered(){
  const q = state.q.toLowerCase();
  return EXPERTS.filter(e =>
    (state.icp==="all"||e.icp===state.icp) &&
    (state.mascot==="all"||e.mascot===state.mascot) &&
    (state.tier==="all"||e.tier===state.tier) &&
    (q===""||String(e.id).toLowerCase().includes(q)||(e.name||"").toLowerCase().includes(q)));
}
function tierChip(tier){
  return `<span class="chip tier-${esc(tier)}"><span class="ico">${TIER_ICON[tier]||"•"}</span>${esc(TIER_LABEL[tier]||tier)}</span>`;
}
function render(){
  const rows = filtered();
  const note = document.getElementById("countNote");
  note.textContent = rows.length > RENDER_CAP
    ? `Showing first ${RENDER_CAP.toLocaleString()} of ${rows.length.toLocaleString()} experts — refine filters or search by agent ID.`
    : `${rows.length.toLocaleString()} expert${rows.length===1?"":"s"}`;

  const res = document.getElementById("results");
  if(rows.length===0){ res.innerHTML = `<p class="empty">No experts match these filters.</p>`; return; }

  let html = "", lastGroup = null, count = 0;
  for(const e of rows){
    if(count >= RENDER_CAP) break;
    const g = e.icp + " ▸ " + e.mascot;
    if(g !== lastGroup){
      if(lastGroup!==null) html += `</div>`;
      const gcount = rows.filter(x=>x.icp===e.icp && x.mascot===e.mascot).length;
      html += `<div class="grouphdr"><h2>${esc(e.icp)} <span class="muted">▸</span> ${esc(e.mascot)}</h2>`+
              `<span class="gc">${gcount} expert${gcount===1?"":"s"}</span><span class="rule"></span></div>`;
      html += `<div class="grid">`;
      lastGroup = g;
    }
    html += card(e);
    count++;
  }
  if(lastGroup!==null) html += `</div>`;
  res.innerHTML = html;
  res.querySelectorAll(".card").forEach(c => c.onclick = () => openModal(c.dataset.id));
}
function advChip(){ return `<span class="chip good"><span class="ico">✓</span>above benchmark</span>`; }
function card(e){
  const coach = e.coach ? ` · coach ${esc(e.coach)}` : "";
  const idtag = (e.name && e.name.trim()) ? `#${esc(e.id)} · ` : "";
  const adv = e.adv ? ` ${advChip()}` : "";
  return `<button class="card" data-id="${esc(e.id)}">
    <div class="row1"><span class="aid">${esc(nameOf(e))}</span>${tierChip(e.tier)}</div>
    <div class="dim">${idtag}${esc(e.mascot)}${coach}</div>
    <div class="focus"><b>${esc(e.topic)}</b>${adv}</div>
    <div class="conv">${esc(e.conv||"")}</div>
  </button>`;
}

/* ---- driver meter ---- */
function meter(d){
  const v = d.v, b = d.b;
  if(v===undefined && b===undefined) return "";
  const scale = Math.max(Math.abs(v||0), Math.abs(b||0)) * 1.12 || 1;
  const vp = Math.max(2, Math.min(100, Math.abs(v||0)/scale*100));
  const bp = Math.max(0, Math.min(100, Math.abs(b||0)/scale*100));
  return `<div class="meter"><div class="fill" style="width:${vp}%"></div>`+
         `<div class="bench" title="benchmark ${fmtNum(b)}" style="left:${bp}%"></div></div>`;
}
const SEV_WORD = {critical:"critical", serious:"elevated", warning:"minor"};
function aboveBench(g, dir){
  if(g===undefined || g===null) return false;
  return dir==="lower_is_better" ? g<=0 : g>=0;  // higher_is_better / default
}
function sevChip(d){
  if(d.cp!==undefined){
    const pct = Math.round(d.cp*100);
    return `<span class="chip critical"><span class="ico">${SEV_ICON.critical}</span>${pct}th percentile in cohort</span>`;
  }
  if(d.g===undefined || d.g===null) return "";
  // On the good side of the benchmark: a strength, not a gap.
  if(aboveBench(d.g, d.dir)) return `<span class="chip good"><span class="ico">${SEV_ICON.good}</span>at/above benchmark</span>`;
  if(d.b===undefined || d.b===0) return "";
  const r = Math.abs(d.g)/Math.abs(d.b);
  const lvl = r>=0.25 ? "critical" : r>=0.10 ? "serious" : "warning";
  // icon + word + number: severity never depends on hue alone.
  return `<span class="chip ${lvl}"><span class="ico">${SEV_ICON[lvl]}</span>${SEV_WORD[lvl]} gap ${fmtNum(d.g)}</span>`;
}
function driverCard(d){
  const nums = [];
  nums.push(`current <b>${fmtNum(d.v)}</b>`);
  nums.push(`benchmark <b>${fmtNum(d.b)}</b>`);
  if(d.g!==undefined) nums.push(`gap <b>${fmtNum(d.g)}</b>`);
  if(d.cs!==undefined) nums.push(`confidence <b>${Math.round(d.cs*100)}%</b>`);
  return `<div class="drv">
    <div class="top"><span class="m">${esc(d.m)}</span>${sevChip(d)}</div>
    ${meter(d)}
    <div class="nums">${nums.join("")}</div>
  </div>`;
}

/* ---- core metrics (this-week snapshot: value vs benchmark + trend) ---- */
function prettyMetric(m){ return String(m==null?"":m).replace(/_/g," "); }
function trendChip(d){
  // Reuse the engine's 8-week trend (slope), falling back to recency_shift; sign = movement.
  let s = (d.t8!==undefined && d.t8!==null) ? d.t8
        : (d.rc!==undefined && d.rc!==null) ? d.rc : null;
  if(s===null) return `<span class="chip"><span class="ico">→</span>trend n/a</span>`;
  // Relative dead-band so tiny slopes read as steady (scales differ across metrics).
  const eps = (d.b!==undefined && d.b!==null && d.b!==0) ? Math.abs(d.b)*0.005 : 0;
  if(Math.abs(s) <= eps) return `<span class="chip"><span class="ico">→</span>steady</span>`;
  const up = s>0;
  // Favorable direction depends on the metric (lower_is_better flips which way is good).
  const favorable = d.dir==="lower_is_better" ? !up : up;
  const cls = favorable ? "good" : "serious";
  return `<span class="chip ${cls}"><span class="ico">${up?"↑":"↓"}</span>trending ${up?"up":"down"}</span>`;
}
/* Plain-text twins of sevChip / trendChip — same wording, no icon markup (for clipboard + table). */
function sevText(d){
  if(d.cp!==undefined) return `${Math.round(d.cp*100)}th percentile in cohort`;
  if(d.g===undefined || d.g===null) return "";
  if(aboveBench(d.g, d.dir)) return "at/above benchmark";
  if(d.b===undefined || d.b===0) return "";
  const r = Math.abs(d.g)/Math.abs(d.b);
  const lvl = r>=0.25 ? "critical" : r>=0.10 ? "serious" : "warning";
  return `${SEV_WORD[lvl]} gap ${fmtNum(d.g)}`;
}
function trendText(d){
  let s = (d.t8!==undefined && d.t8!==null) ? d.t8
        : (d.rc!==undefined && d.rc!==null) ? d.rc : null;
  if(s===null) return "trend n/a";
  const eps = (d.b!==undefined && d.b!==null && d.b!==0) ? Math.abs(d.b)*0.005 : 0;
  if(Math.abs(s) <= eps) return "steady";
  return s>0 ? "trending up" : "trending down";
}
/* Core metrics render as a table (no bar meters) so the on-screen view matches the paste. */
function coreMetricsTable(rows){
  const body = rows.map(d=>`<tr>`+
    `<td class="m">${esc(prettyMetric(d.m))}</td>`+
    `<td class="num">${fmtNum(d.v)}</td>`+
    `<td class="num">${fmtNum(d.b)}</td>`+
    `<td>${sevChip(d)}</td>`+
    `<td>${trendChip(d)}</td>`+
  `</tr>`).join("");
  return `<table class="cmtable"><thead><tr>`+
    `<th>Metric</th><th class="num">This week</th><th class="num">Benchmark</th>`+
    `<th>vs. benchmark</th><th>Trend</th></tr></thead><tbody>${body}</tbody></table>`;
}
/* Which week the core metrics are from — and a callout when it is not the current week. */
function cmNoteText(e){
  if(!e.cmp) return "";
  return e.cms
    ? `Latest available data — week ending ${e.cmp} (no data for the current coaching week).`
    : `Data for week ending ${e.cmp}.`;
}
function cmNoteHtml(e){
  const t = cmNoteText(e);
  if(!t) return "";
  return `<div class="cmnote${e.cms?" stale":""}">${e.cms?"⚠ ":""}${esc(t)}</div>`;
}

/* ---- copy-to-clipboard: block headers, writer, per-block serializers ---- */
const CLIP_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"></rect><path d="M5 15V5a2 2 0 0 1 2-2h10"></path></svg>`;
const CHECK_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"></path></svg>`;
function secHead(title, key){
  return `<div class="h"><span>${esc(title)}</span>`+
    `<button class="copybtn" data-copy="${key}" title="Copy ${esc(title)}" aria-label="Copy ${esc(title)}">${CLIP_ICON}</button></div>`;
}

function fallbackCopy(html, text){
  try{
    const div = document.createElement("div");
    div.setAttribute("contenteditable","true");
    div.style.cssText = "position:fixed;left:-9999px;top:0;white-space:pre-wrap";
    div.innerHTML = html;
    document.body.appendChild(div);
    const range = document.createRange();
    range.selectNodeContents(div);
    const sel = window.getSelection();
    sel.removeAllRanges(); sel.addRange(range);
    const ok = document.execCommand("copy");
    sel.removeAllRanges(); document.body.removeChild(div);
    if(ok) return true;
  }catch(e){}
  try{
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.cssText = "position:fixed;left:-9999px;top:0";
    document.body.appendChild(ta); ta.focus(); ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  }catch(e){ return false; }
}
function writeClipboard(html, text){
  if(navigator.clipboard && window.ClipboardItem){
    try{
      const item = new ClipboardItem({
        "text/html": new Blob([html], {type:"text/html"}),
        "text/plain": new Blob([text], {type:"text/plain"})
      });
      return navigator.clipboard.write([item]).catch(()=>fallbackCopy(html, text));
    }catch(e){ return Promise.resolve(fallbackCopy(html, text)); }
  }
  return Promise.resolve(fallbackCopy(html, text));
}

/* Rich-text building blocks — inline styles only, so formatting survives paste into external editors. */
const _WRAP = s => `<div style="font-family:'Segoe UI',Arial,sans-serif;font-size:13px;color:#111;line-height:1.5">${s}</div>`;
const _H = t => `<p style="font-weight:700;margin:0 0 6px">${esc(t)}</p>`;
const _TB = "border-collapse:collapse;font-size:13px";
const _TH = "text-align:left;padding:4px 16px 5px 0;border-bottom:1px solid #bbb;color:#555;font-weight:600;white-space:nowrap";
const _THR = _TH + ";text-align:right";
const _TD = "padding:5px 16px 5px 0;border-bottom:1px solid #e5e5e5;vertical-align:top";
const _TDR = _TD + ";text-align:right;font-variant-numeric:tabular-nums";
function _tail(d){ return [sevText(d), trendText(d)].filter(Boolean).join(", "); }

function buildCopy(key, e){
  if(key==="cm"){
    const rows = e.cm||[];
    const note = cmNoteText(e);
    const noteHtml = note ? `<p style="margin:0 0 6px;color:${e.cms?"#8a6100":"#555"}${e.cms?";font-weight:600":""}">${esc(note)}</p>` : "";
    const rhtml = rows.map(d=>`<tr>`+
      `<td style="${_TD}"><b>${esc(prettyMetric(d.m))}</b></td>`+
      `<td style="${_TDR}">${fmtNum(d.v)}</td>`+
      `<td style="${_TDR}">${fmtNum(d.b)}</td>`+
      `<td style="${_TD}">${esc(sevText(d))}</td>`+
      `<td style="${_TD}">${esc(trendText(d))}</td></tr>`).join("");
    const html = _WRAP(_H("Core metrics this week")+noteHtml+
      `<table style="${_TB}"><thead><tr>`+
      `<th style="${_TH}">Metric</th><th style="${_THR}">This week</th><th style="${_THR}">Benchmark</th>`+
      `<th style="${_TH}">vs. benchmark</th><th style="${_TH}">Trend</th></tr></thead><tbody>${rhtml}</tbody></table>`);
    const text = "Core metrics this week\n" + (note ? note+"\n" : "") + rows.map(d=>{
      const t = _tail(d);
      return `- ${prettyMetric(d.m)}: ${fmtNum(d.v)} (benchmark ${fmtNum(d.b)})${t?` — ${t}`:""}`;
    }).join("\n");
    return {html, text};
  }
  if(key==="why-this" || key==="why-now" || key==="why-not"){
    const m = {"why-this":["Why this", e.why.this],
               "why-now":["Why now", e.why.now],
               "why-not":["Why not something else", e.why.not]};
    const [label, body] = m[key];
    return {html: _WRAP(_H(label)+`<p style="margin:0">${esc(body||"")}</p>`),
            text: `${label}\n${body||""}`};
  }
  if(key==="theme"){
    const t = e.theme || {};
    const dm = Array.isArray(t.dm) ? t.dm : [];
    const line = `${t.nd} of ${t.nm} related behaviors are deficient together: ${dm.map(prettyMetric).join(", ")}`;
    return {html: _WRAP(_H("Theme pattern")+`<p style="margin:0">${esc(line)}</p>`),
            text: `Theme pattern\n${line}`};
  }
  if(key==="drivers"){
    const rows = e.drivers||[];
    if(!rows.length) return {html:_WRAP(_H("Evidence & relevant numbers")+`<p style="margin:0">No driver detail recorded.</p>`),
                             text:"Evidence & relevant numbers\nNo driver detail recorded."};
    const conf = d => (d.cs!==undefined && d.cs!==null) ? `${Math.round(d.cs*100)}%` : "—";
    const rhtml = rows.map(d=>`<tr>`+
      `<td style="${_TD}"><b>${esc(prettyMetric(d.m))}</b></td>`+
      `<td style="${_TDR}">${fmtNum(d.v)}</td>`+
      `<td style="${_TDR}">${fmtNum(d.b)}</td>`+
      `<td style="${_TDR}">${fmtNum(d.g)}</td>`+
      `<td style="${_TDR}">${conf(d)}</td></tr>`).join("");
    const html = _WRAP(_H("Evidence & relevant numbers")+
      `<table style="${_TB}"><thead><tr>`+
      `<th style="${_TH}">Metric</th><th style="${_THR}">Current</th><th style="${_THR}">Benchmark</th>`+
      `<th style="${_THR}">Gap</th><th style="${_THR}">Confidence</th></tr></thead><tbody>${rhtml}</tbody></table>`);
    const text = "Evidence & relevant numbers\n" + rows.map(d=>
      `- ${prettyMetric(d.m)}: current ${fmtNum(d.v)}, benchmark ${fmtNum(d.b)}, gap ${fmtNum(d.g)}, confidence ${conf(d)}`).join("\n");
    return {html, text};
  }
  if(key==="alts"){
    const rows = e.comp||[];
    if(!rows.length) return {html:_WRAP(_H("Alternatives considered")+`<p style="margin:0">No competing alternatives were recorded for this decision.</p>`),
                             text:"Alternatives considered\nNo competing alternatives were recorded for this decision."};
    const html = _WRAP(_H("Alternatives considered")+rows.map(c=>
      `<p style="margin:0 0 6px"><b>${esc(c.topic)}</b>${c.metric?` — driver ${esc(c.metric)}`:""}`+
      `${c.reason?`<br><span style="color:#555">${esc(c.reason)}</span>`:""}</p>`).join(""));
    const text = "Alternatives considered\n" + rows.map(c=>
      `- ${c.topic}${c.metric?` (driver ${c.metric})`:""}${c.reason?`: ${c.reason}`:""}`).join("\n");
    return {html, text};
  }
  if(key==="excl"){
    const rows = e.excl||[];
    if(!rows.length) return {html:_WRAP(_H("Data considered but excluded")+`<p style="margin:0">No signals were excluded for this expert.</p>`),
                             text:"Data considered but excluded\nNo signals were excluded for this expert."};
    const meta = x => `value ${fmtNum(x.v)}${(x.conf!==null&&x.conf!==undefined)?`, confidence ${Math.round(x.conf*100)}%`:""}`;
    const reasons = x => (Array.isArray(x.reasons)?x.reasons:[]).join(", ");
    const html = _WRAP(_H("Data considered but excluded")+rows.map(x=>
      `<p style="margin:0 0 6px"><b>${esc(prettyMetric(x.m))}</b> — ${esc(meta(x))}`+
      `${reasons(x)?`<br><span style="color:#555">reasons: ${esc(reasons(x))}</span>`:""}</p>`).join(""));
    const text = "Data considered but excluded\n" + rows.map(x=>
      `- ${prettyMetric(x.m)}: ${meta(x)}${reasons(x)?` — reasons: ${reasons(x)}`:""}`).join("\n");
    return {html, text};
  }
  return {html:"", text:""};
}

/* ---- modal ---- */
function openModal(id){
  const e = byId.get(id);
  if(!e) return;
  const coach = e.coach ? `<span>coach ${esc(e.coach)}</span>` : "";
  const theme = e.theme ? `<div class="sec">${secHead("Theme pattern","theme")}
      <p style="margin:0 0 8px;font-size:13px;color:var(--text-2)">${e.theme.nd} of ${e.theme.nm} related behaviors are deficient together:</p>
      <div>${(Array.isArray(e.theme.dm)?e.theme.dm:[]).map(m=>`<span class="tag">${esc(m)}</span>`).join("")}</div></div>` : "";

  const coreMetrics = (e.cm||[]).length
    ? `<div class="sec">${secHead("Core metrics this week","cm")}${cmNoteHtml(e)}${coreMetricsTable(e.cm)}</div>`
    : "";  // hidden when no core metrics have data this week

  const drivers = (e.drivers||[]).length
    ? `<div class="legend"><span><span class="sw"></span>current value</span><span><span class="bk"></span>benchmark</span></div>`
      + e.drivers.map(driverCard).join("")
    : `<p class="empty">No driver detail recorded.</p>`;

  const alts = (e.comp||[]).length
    ? e.comp.map(c=>`<div class="altrow"><div><div class="t">${esc(c.topic)}</div>`+
        `${c.metric?`<div class="r">driver ${esc(c.metric)}</div>`:""}</div>`+
        `<div class="r" style="max-width:52%;text-align:right">${esc(c.reason||"")}</div></div>`).join("")
    : `<p class="empty">No competing alternatives were recorded for this decision.</p>`;

  const excl = (e.excl||[]).length
    ? e.excl.map(x=>`<div class="exrow"><div><span class="m">${esc(x.m)}</span>`+
        `<div class="r">value ${fmtNum(x.v)}${x.conf!==null&&x.conf!==undefined?` · confidence ${Math.round(x.conf*100)}%`:""}</div></div>`+
        `<div class="r">${(Array.isArray(x.reasons)?x.reasons:[]).map(r=>`<span class="tag">${esc(r)}</span>`).join("")}</div></div>`).join("")
    : `<p class="empty">No signals were excluded for this expert.</p>`;

  const p = e.prov||{};
  document.getElementById("modal").innerHTML = `
    <div class="mhead">
      <button class="close" id="closeBtn" aria-label="Close">✕</button>
      <div class="eyebrow">Decision receipt</div>
      <h3 id="mTitle">${esc(nameOf(e))}</h3>
      <div class="dims">${tierChip(e.tier)}<span>#${esc(e.id)}</span><span>${esc(e.mascot)}</span><span>${esc(e.icp)}</span>${coach}</div>
    </div>
    <div class="mbody">
      <div class="focusband">
        <div class="k">Coaching focus</div>
        <div class="topic">${esc(e.topic)}</div>
        <div class="conv">${esc(e.conv||"")}</div>
      </div>
      ${e.adv ? `<div class="advbanner">${advChip()}<span>This expert is at or above benchmark on the recommended behavior: reinforcement of a strength, not a performance gap.</span></div>` : ""}

      ${coreMetrics}

      <div class="sec">${secHead("Why this","why-this")}
        <div class="why this"><div class="lab">WHY THIS</div><p>${esc(e.why.this)}</p></div></div>
      <div class="sec">${secHead("Why now","why-now")}
        <div class="why now"><div class="lab">WHY NOW</div><p>${esc(e.why.now)}</p></div></div>
      <div class="sec">${secHead("Why not something else","why-not")}
        <div class="why not"><div class="lab">WHY NOT SOMETHING ELSE</div><p>${esc(e.why.not)}</p></div></div>

      ${theme}

      <div class="sec">${secHead("Evidence & relevant numbers","drivers")}${drivers}</div>
      <div class="sec">${secHead("Alternatives considered","alts")}${alts}</div>
      <div class="sec">${secHead("Data considered but excluded","excl")}${excl}</div>

      <div class="prov">
        <span>config ${esc(p.cv||"?")}</span><span>hash ${esc(p.ch||"?")}</span>
        <span>snapshot ${esc(p.ds||"?")}</span><span>engine ${esc(p.ev||"?")}</span>
      </div>
    </div>`;
  const ov = document.getElementById("overlay");
  ov.classList.add("open");
  document.getElementById("closeBtn").onclick = closeModal;
  document.getElementById("closeBtn").focus();
  document.querySelectorAll("#modal .copybtn").forEach(btn => {
    btn.onclick = () => {
      const {html, text} = buildCopy(btn.dataset.copy, e);
      Promise.resolve(writeClipboard(html, text)).then(() => {
        btn.classList.add("done"); btn.innerHTML = CHECK_ICON;
        setTimeout(() => { btn.classList.remove("done"); btn.innerHTML = CLIP_ICON; }, 1200);
      });
    };
  });
  if(location.hash !== "#a="+id) history.replaceState(null,"","#a="+id);
}
function closeModal(){
  document.getElementById("overlay").classList.remove("open");
  if(location.hash.startsWith("#a=")) history.replaceState(null,"","#");
}
document.getElementById("overlay").addEventListener("click", e => { if(e.target.id==="overlay") closeModal(); });
document.addEventListener("keydown", e => { if(e.key==="Escape") closeModal(); });

/* ---- theme toggle ---- */
(function(){
  const btn = document.getElementById("themeToggle");
  btn.onclick = () => {
    const cur = document.documentElement.getAttribute("data-theme");
    const next = cur==="dark" ? "light" : cur==="light" ? "dark"
      : (matchMedia("(prefers-color-scheme:dark)").matches ? "light" : "dark");
    document.documentElement.setAttribute("data-theme", next);
  };
})();

/* ---- deep-link: open a specific expert via #a=<agent_id> ---- */
function openFromHash(){
  const m = /^#a=(.+)$/.exec(location.hash);
  if(m && byId.has(decodeURIComponent(m[1]))) openModal(decodeURIComponent(m[1]));
}
window.addEventListener("hashchange", openFromHash);

/* ---- boot ---- */
renderTiles();
buildFilters();
render();
openFromHash();
</script>
</body>
</html>
"""
