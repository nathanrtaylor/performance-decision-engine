"""
Self-contained HTML dashboard summarizing a pipeline run's recommendations.

Written as part of the standard output package (dashboard.html). No external/runtime
dependencies: a single HTML file with inline CSS (light + dark), so it opens anywhere.

Design follows the dataviz method:
  - topic counts are *magnitude* -> a single blue hue ranked bar (not categorical cycling).
  - metric warnings are *status* -> the reserved status palette, always icon + label (never
    color alone), so meaning survives colorblindness / print.

Sections:
  1. Summary tiles (totals + provenance)
  2. Recommendations by topic (ranked bars)
  3. Splits by icp_client (topic x client matrix) and by mascot (top groups)
  4. Metric health / warning signs (benchmark breaches + data-quality flags)
  5. Data issues (excluded signals, coverage, dampening)
"""
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from cde.utils.logging import get_logger

log = get_logger(__name__)

# --- validated design tokens (dataviz reference palette) -----------------------------------------
_CSS = """
:root{
  --surface-1:#fcfcfb; --page:#f9f9f7; --text-1:#0b0b0b; --text-2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --baseline:#c3c2b7; --border:rgba(11,11,11,.10);
  --series:#2a78d6; --track:#eef1f5;
  --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
}
@media (prefers-color-scheme:dark){:root{
  --surface-1:#1a1a19; --page:#0d0d0d; --text-1:#fff; --text-2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --baseline:#383835; --border:rgba(255,255,255,.10);
  --series:#3987e5; --track:#26262400;
}}
@media (prefers-color-scheme:dark){:root{--track:#242422;}}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--text-1);
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif;font-size:14px;line-height:1.45}
.wrap{max-width:1120px;margin:0 auto;padding:28px 22px 64px}
h1{font-size:22px;margin:0 0 2px} h2{font-size:15px;margin:34px 0 10px;letter-spacing:.01em}
.sub{color:var(--text-2);font-size:12.5px;margin:0 0 4px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:16px}
.tile{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:14px 16px}
.tile .v{font-size:26px;font-weight:650} .tile .k{color:var(--text-2);font-size:12px;margin-top:2px}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:6px 4px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--grid);vertical-align:middle}
th{color:var(--muted);font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.03em}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
.barrow td{border-bottom:none;padding:5px 12px}
.bar-track{background:var(--track);border-radius:5px;height:16px;width:100%;min-width:80px}
.bar-fill{background:var(--series);height:16px;border-radius:0 4px 4px 0;min-width:2px}
.chip{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:600;
  padding:2px 9px;border-radius:999px;border:1px solid var(--border);white-space:nowrap}
.chip .ico{font-size:11px;line-height:1}
.chip.good{color:var(--good)} .chip.warning{color:#8a6100} .chip.serious{color:#a2432a} .chip.critical{color:var(--critical)}
@media (prefers-color-scheme:dark){.chip.warning{color:var(--warning)} .chip.serious{color:var(--serious)}}
.chip.good .ico{color:var(--good)} .chip.warning .ico{color:var(--warning)}
.chip.serious .ico{color:var(--serious)} .chip.critical .ico{color:var(--critical)}
.foot{color:var(--muted);font-size:11.5px;margin-top:8px}
.small{color:var(--text-2);font-size:12px}
.note{color:var(--muted);font-size:12px;margin:6px 2px 0}
"""

_ICONS = {"good": "✓", "warning": "⚠", "serious": "▲", "critical": "✕"}


def _esc(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _fmt_int(x: Any) -> str:
    try:
        return f"{int(round(float(x))):,}"
    except (TypeError, ValueError):
        return "-"


def _fmt_pct(x: Any, digits: int = 0) -> str:
    try:
        return f"{float(x) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "-"


def _fmt_num(x: Any, digits: int = 3) -> str:
    try:
        v = float(x)
        return "-" if pd.isna(v) else f"{v:.{digits}g}"
    except (TypeError, ValueError):
        return "-"


def _chip(level: str, label: str) -> str:
    ico = _ICONS.get(level, "")
    return f'<span class="chip {level}"><span class="ico">{ico}</span>{_esc(label)}</span>'


def _tile(value: str, key: str) -> str:
    return f'<div class="tile"><div class="v">{value}</div><div class="k">{_esc(key)}</div></div>'


def _bar(count: int, max_count: int) -> str:
    pct = 0.0 if max_count <= 0 else 100.0 * count / max_count
    return f'<div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%"></div></div>'


def _unwrap(obj: Any, root: str) -> Dict[str, Any]:
    if isinstance(obj, dict) and isinstance(obj.get(root), dict):
        return obj[root]
    return obj if isinstance(obj, dict) else {}


# -------------------------------------------------------------------------------------------------

def _recent_nonnull(df: Optional[pd.DataFrame], period_col: str, col: str) -> Optional[pd.Series]:
    """agent_id -> most-recent non-null value of ``col`` (agents/signals attribute lookup)."""
    if df is None or getattr(df, "empty", True) or col not in df.columns or "agent_id" not in df.columns:
        return None
    keep = ["agent_id", col] + ([period_col] if period_col in df.columns else [])
    d = df[keep].copy()
    d["agent_id"] = d["agent_id"].astype(str)
    d = d.dropna(subset=[col])
    if d.empty:
        return None
    if period_col in d.columns:
        # period may be mixed dtype (datetime from one source, str from another); coerce to sort safely
        d[period_col] = pd.to_datetime(d[period_col], errors="coerce")
        d = d.sort_values(period_col)
    return d.drop_duplicates("agent_id", keep="last").set_index("agent_id")[col]


def build_dashboard_html(
    recommendations: pd.DataFrame,
    signals: Optional[pd.DataFrame],
    config: Dict[str, Any],
    excluded_signals: Optional[pd.DataFrame] = None,
    candidates: Optional[pd.DataFrame] = None,
    agents: Optional[pd.DataFrame] = None,
    generated_at: Optional[str] = None,
) -> str:
    meta = (config or {}).get("meta") or {}
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")

    recs = recommendations.copy() if recommendations is not None else pd.DataFrame()
    for c in ("agent_id", "topic"):
        if c in recs.columns:
            recs[c] = recs[c].astype(str)

    sig = signals.copy() if signals is not None and not signals.empty else pd.DataFrame()
    if not sig.empty and "agent_id" in sig.columns:
        sig["agent_id"] = sig["agent_id"].astype(str)
        # period can be mixed dtype across sources; normalize so downstream sort/max/== are safe
        if "period" in sig.columns:
            sig["period"] = pd.to_datetime(sig["period"], errors="coerce")

    # --- attach icp_client / mascot: prefer the agents dimension table, fall back to signals ---
    # (signals enrichment is sparse, so take the most-recent NON-NULL value per agent from either.)
    for col in ("icp_client", "mascot"):
        from_agents = _recent_nonnull(agents, "week_ending", col)
        from_signals = _recent_nonnull(sig, "period", col)
        combined = from_agents
        if from_signals is not None:
            combined = from_signals if combined is None else combined.combine_first(from_signals)
        if combined is not None and not recs.empty and "agent_id" in recs.columns:
            recs[col] = recs["agent_id"].map(combined)
        elif col not in recs.columns:
            recs[col] = np.nan
        recs[col] = recs[col].fillna("(unknown)").astype(str)

    total = len(recs)
    parts: list[str] = []

    # ============================ 1. SUMMARY TILES ============================
    n_agents = recs["agent_id"].nunique() if "agent_id" in recs.columns else 0
    n_topics = recs["topic"].nunique() if "topic" in recs.columns else 0
    n_dampened = 0
    if candidates is not None and "dampened" in getattr(candidates, "columns", []):
        n_dampened = int(pd.to_numeric(candidates["dampened"], errors="coerce").fillna(0).astype(bool).sum())

    tiles = [
        _tile(_fmt_int(total), "Recommendations"),
        _tile(_fmt_int(n_agents), "Agents coached"),
        _tile(_fmt_int(n_topics), "Distinct topics"),
        _tile(_fmt_int(n_dampened), "Dampened candidates"),
        _tile(_esc(meta.get("data_snapshot", "-")), "Data snapshot"),
        _tile(_esc(meta.get("version", "-")), "Config version"),
    ]
    parts.append(
        f"<h1>Coaching Decision Engine — Run Dashboard</h1>"
        f'<p class="sub">Snapshot <b>{_esc(meta.get("data_snapshot","-"))}</b> · '
        f'engine {_esc(meta.get("engine_version","-"))} · generated {_esc(generated_at)}</p>'
        f'<div class="tiles">{"".join(tiles)}</div>'
    )

    if total == 0:
        parts.append('<p class="note">No recommendations were produced for this run.</p>')
        return _page("".join(parts))

    # ============================ 2. BY TOPIC ============================
    by_topic = (
        recs.groupby("topic")
        .agg(recs_n=("topic", "size"), avg_priority=("priority_score", "mean"))
        .sort_values("recs_n", ascending=False)
    )
    max_n = int(by_topic["recs_n"].max())
    rows = []
    for topic, r in by_topic.iterrows():
        n = int(r["recs_n"])
        rows.append(
            f'<tr class="barrow"><td>{_esc(topic)}</td>'
            f'<td class="num">{_fmt_int(n)}</td>'
            f'<td class="num">{_fmt_pct(n/total,1)}</td>'
            f'<td style="width:42%">{_bar(n,max_n)}</td>'
            f'<td class="num">{_fmt_num(r["avg_priority"],3)}</td></tr>'
        )
    parts.append(
        "<h2>Recommendations by topic</h2>"
        '<div class="card"><table><thead><tr>'
        '<th>Topic</th><th class="num">Recs</th><th class="num">Share</th>'
        '<th>Distribution</th><th class="num">Avg priority</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )

    # conversation type (bonus, compact)
    if "conversation_type" in recs.columns:
        ct = recs["conversation_type"].value_counts()
        chips = " ".join(
            f'<span class="chip"><b>{_fmt_int(v)}</b>&nbsp;{_esc(k)}</span>' for k, v in ct.items()
        )
        parts.append(f'<p class="note">Conversation types: {chips}</p>')

    # ============================ 3a. SPLIT BY icp_client (matrix) ============================
    parts.append(_matrix_section("Split by icp_client", recs, "icp_client", total))

    # ============================ 3b. SPLIT BY mascot (top groups) ============================
    parts.append(_group_section("Split by mascot", recs, "mascot", total, top_n=25))

    # ============================ 4. METRIC HEALTH ============================
    parts.append(_metric_health_section(sig, recs, config))

    # ============================ 5. RECENCY DAMPENING ============================
    parts.append(_dampening_section(candidates, recs, config))

    # ============================ 6. DATA ISSUES ============================
    parts.append(_data_issues_section(sig, recs, excluded_signals, candidates))

    parts.append(
        f'<p class="foot">Coaching Decision Engine · config {_esc(meta.get("version","-"))} · '
        f'engine {_esc(meta.get("engine_version","-"))} · snapshot {_esc(meta.get("data_snapshot","-"))}</p>'
    )
    return _page("".join(parts))


def _matrix_section(title: str, recs: pd.DataFrame, dim: str, total: int) -> str:
    """topic x dimension count matrix — good for low-cardinality dims (icp_client)."""
    if dim not in recs.columns:
        return ""
    ct = pd.crosstab(recs["topic"], recs[dim])
    # order columns by total recs, rows by total recs
    ct = ct.loc[ct.sum(axis=1).sort_values(ascending=False).index,
                ct.sum(axis=0).sort_values(ascending=False).index]
    cols = list(ct.columns)
    head = "".join(f'<th class="num">{_esc(c)}</th>' for c in cols)
    body = []
    for topic, r in ct.iterrows():
        cells = "".join(f'<td class="num">{_fmt_int(v)}</td>' for v in r)
        body.append(f"<tr><td>{_esc(topic)}</td>{cells}<td class='num'><b>{_fmt_int(int(r.sum()))}</b></td></tr>")
    totals = "".join(f'<td class="num"><b>{_fmt_int(int(ct[c].sum()))}</b></td>' for c in cols)
    body.append(f'<tr><td><b>Total</b></td>{totals}<td class="num"><b>{_fmt_int(total)}</b></td></tr>')
    return (
        f"<h2>{_esc(title)}</h2>"
        '<div class="card"><table><thead><tr><th>Topic</th>'
        f'{head}<th class="num">Total</th></tr></thead><tbody>{"".join(body)}</tbody></table></div>'
    )


def _group_section(title: str, recs: pd.DataFrame, dim: str, total: int, top_n: int = 25) -> str:
    """Per-group totals + top topic — good for high-cardinality dims (mascot)."""
    if dim not in recs.columns:
        return ""
    g = recs.groupby(dim)
    summary = g.agg(recs_n=(dim, "size"), topics=("topic", "nunique")).sort_values("recs_n", ascending=False)
    top_topic = g["topic"].agg(lambda s: s.value_counts().idxmax() if len(s) else "-")
    top_topic_n = g["topic"].agg(lambda s: int(s.value_counts().max()) if len(s) else 0)

    n_groups = len(summary)
    shown = summary.head(top_n)
    max_n = int(shown["recs_n"].max()) if len(shown) else 0
    rows = []
    for grp, r in shown.iterrows():
        n = int(r["recs_n"])
        rows.append(
            f'<tr class="barrow"><td>{_esc(grp)}</td>'
            f'<td class="num">{_fmt_int(n)}</td>'
            f'<td class="num">{_fmt_pct(n/total,1)}</td>'
            f'<td style="width:34%">{_bar(n,max_n)}</td>'
            f'<td class="num">{_fmt_int(int(r["topics"]))}</td>'
            f'<td>{_esc(top_topic.get(grp,"-"))} <span class="small">({_fmt_int(top_topic_n.get(grp,0))})</span></td></tr>'
        )
    note = ""
    if n_groups > top_n:
        note = f'<p class="note">Showing top {top_n} of {_fmt_int(n_groups)} {_esc(dim)} groups by recommendation count.</p>'
    return (
        f"<h2>{_esc(title)}</h2>"
        '<div class="card"><table><thead><tr>'
        f'<th>{_esc(dim)}</th><th class="num">Recs</th><th class="num">Share</th>'
        '<th>Distribution</th><th class="num">Topics</th><th>Top topic</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>{note}'
    )


def _metric_directions(config: Dict[str, Any]) -> Dict[str, str]:
    mc = _unwrap((config or {}).get("metric_catalog") or {}, "metric_catalog")
    metrics = mc.get("metrics") or {}
    return {m: (v.get("direction") or "higher_is_better") for m, v in metrics.items()}


def _metric_health_section(sig: pd.DataFrame, recs: pd.DataFrame, config: Dict[str, Any]) -> str:
    """Per-metric warning signs: benchmark breaches + benchmark-placement + data-quality flags."""
    if sig is None or sig.empty or "metric" not in sig.columns:
        return ""
    df = sig.copy()
    if "period" in df.columns:  # cross-section on the latest period
        df = df[df["period"] == df["period"].max()]
    directions = _metric_directions(config)
    recs_by_metric = recs["metric"].value_counts().to_dict() if "metric" in recs.columns else {}

    rows = []
    order = []
    for metric, g in df.groupby("metric"):
        val = pd.to_numeric(g.get("value"), errors="coerce")
        bench = pd.to_numeric(g.get("benchmark"), errors="coerce")
        gap = pd.to_numeric(g.get("gap"), errors="coerce")
        direction = directions.get(metric) or (str(g["direction"].dropna().iloc[0]) if "direction" in g and g["direction"].notna().any() else "higher_is_better")

        n = int(len(g))
        null_rate = float(val.isna().mean()) if n else 0.0
        both = val.notna() & bench.notna()
        at_or_below = float((val[both] <= bench[both]).mean()) if both.any() else float("nan")
        # breach = worse than benchmark, direction-aware (uses signed gap)
        gv = gap.dropna()
        if len(gv):
            breach_rate = float((gv > 0).mean()) if direction == "lower_is_better" else float((gv < 0).mean())
        else:
            breach_rate = float("nan")
        bval = float(bench.dropna().median()) if bench.notna().any() else None

        extreme = (not np.isnan(at_or_below)) and (at_or_below <= 0.05 or at_or_below >= 0.95)
        if extreme:
            level, label = "critical", "Benchmark misconfig?"
        elif null_rate >= 0.25:
            level, label = "warning", "High null rate"
        elif not np.isnan(breach_rate) and breach_rate >= 0.60:
            level, label = "serious", "Widespread deficit"
        else:
            level, label = "good", "Healthy"

        # sort key: worst first
        sev = {"critical": 0, "serious": 1, "warning": 2, "good": 3}[level]
        order.append((sev, -(0 if np.isnan(breach_rate) else breach_rate), metric))
        rows.append((metric, {
            "direction": direction, "bval": bval, "n": n,
            "breach": breach_rate, "null": null_rate, "atb": at_or_below,
            "recs": int(recs_by_metric.get(metric, 0)), "level": level, "label": label,
        }))

    rows = [r for _, r in sorted(zip(order, rows), key=lambda t: t[0])]
    tr = []
    for metric, d in rows:
        tr.append(
            f"<tr><td>{_esc(metric)}</td>"
            f'<td class="small">{_esc(d["direction"].replace("_"," "))}</td>'
            f'<td class="num">{_fmt_num(d["bval"],4)}</td>'
            f'<td class="num">{_fmt_int(d["n"])}</td>'
            f'<td class="num">{_fmt_pct(d["breach"],0)}</td>'
            f'<td class="num">{_fmt_pct(d["null"],0)}</td>'
            f'<td class="num">{_fmt_pct(d["atb"],0)}</td>'
            f'<td class="num">{_fmt_int(d["recs"])}</td>'
            f'<td>{_chip(d["level"], d["label"])}</td></tr>'
        )
    return (
        "<h2>Metric health &amp; warning signs</h2>"
        '<div class="card"><table><thead><tr>'
        '<th>Metric</th><th>Direction</th><th class="num">Benchmark</th><th class="num">Agents</th>'
        '<th class="num">% worse than bench</th><th class="num">% null value</th>'
        '<th class="num">% &le; bench</th><th class="num">Recs driven</th><th>Status</th>'
        f'</tr></thead><tbody>{"".join(tr)}</tbody></table></div>'
        '<p class="note">"% &le; bench" near 0% or 100% signals a benchmark on the wrong scale '
        '(same check as the runtime benchmark guardrail). "% worse than bench" is direction-aware '
        'systemic underperformance. Status is icon + label so it survives print / colorblindness.</p>'
    )


def _dampening_section(
    candidates: Optional[pd.DataFrame],
    recs: pd.DataFrame,
    config: Dict[str, Any],
) -> str:
    """
    Recency-dampening visibility: the rule/config, WHICH topics were dampened (and how heavily),
    and the IMPACT on recommendations.

    Dampening (src/cde/prioritization/dampening.py) flags a candidate when the same agent+topic was
    coached within ``dampening.periods`` weeks. In ``multiply`` mode the dampened rows stay in
    ``candidates`` with a halved ``priority_score``, so we can reconstruct the pre-dampening winner
    (score / multiplier) and count how many recommendations flipped. In ``suppress`` mode the rows are
    dropped upstream, so only the config + count are shown.
    """
    if candidates is None or getattr(candidates, "empty", True):
        return ""
    if "dampened" not in getattr(candidates, "columns", []):
        return ""

    damp_cfg = (config or {}).get("dampening") or {}
    mode = str(damp_cfg.get("mode", "multiply")).lower()
    periods = damp_cfg.get("periods", 2)
    mult = damp_cfg.get("multiplier", 0.5)
    try:
        mult = float(mult)
    except (TypeError, ValueError):
        mult = 0.5

    cand = candidates.copy()
    damp_mask = pd.to_numeric(cand["dampened"], errors="coerce").fillna(0).astype(bool)
    n_damp = int(damp_mask.sum())
    total_cand = len(cand)

    mech = (
        '<p class="note">Rule: a candidate is dampened when the same agent was coached on that topic '
        f'within <b>{_esc(periods)}</b> week(s) of the decision period. Mode <b>{_esc(mode)}</b>'
        + (f' (priority x {_fmt_num(mult, 2)}, kept in contention)' if mode == "multiply"
           else " (candidate removed from contention)")
        + ".</p>"
    )

    # --- suppress mode or nothing dampened: config + count only (detail not reconstructable) ---
    if mode == "suppress" or n_damp == 0:
        tiles = [_tile(_fmt_int(n_damp), "Dampened candidates")]
        if mode == "suppress":
            note = (
                '<p class="note">Mode is <b>suppress</b>: dampened candidates are removed before '
                "topic_candidates.csv is written, so per-topic and impact detail is not available "
                "here. Run in <b>multiply</b> mode to retain them for analysis.</p>"
            )
        else:
            note = '<p class="note">No candidates were dampened this run.</p>'
        return "<h2>Recency dampening</h2>" + mech + f'<div class="tiles">{"".join(tiles)}</div>' + note

    # --- multiply mode with dampened rows present ---
    dd = cand[damp_mask]
    n_agents = dd["agent_id"].nunique() if "agent_id" in dd.columns else 0

    # Impact: reconstruct pre-dampening winner per (agent, period) and count flips.
    flips = still_top = n_reco = None
    have_impact = (
        mult > 0
        and {"priority_score", "agent_id", "period", "topic"}.issubset(cand.columns)
    )
    if have_impact:
        c = cand.copy()
        c["_post"] = pd.to_numeric(c["priority_score"], errors="coerce")
        c = c[c["_post"].notna()]
        if len(c):
            dm = damp_mask.loc[c.index]
            c["_pre"] = c["_post"].where(~dm, c["_post"] / mult)
            grp = ["agent_id", "period"]
            post_idx = c.groupby(grp)["_post"].idxmax()
            pre_idx = c.groupby(grp)["_pre"].idxmax()
            post_win = c.loc[post_idx, grp + ["topic"]].set_index(grp)["topic"]
            pre_win = c.loc[pre_idx, grp + ["topic"]].set_index(grp)["topic"]
            j = pd.concat([post_win.rename("post"), pre_win.rename("pre")], axis=1)
            n_reco = int(len(j))
            flips = int((j["post"] != j["pre"]).sum())
            still_top = int(dm.loc[post_idx].sum())
        else:
            have_impact = False

    tiles = [
        _tile(_fmt_int(n_damp), f"Dampened ({_fmt_pct(n_damp / total_cand, 1)} of candidates)"),
        _tile(_fmt_int(n_agents), "Agents affected"),
    ]
    if have_impact:
        tiles.append(_tile(_fmt_int(flips), "Recommendations changed"))
        tiles.append(_tile(_fmt_int(still_top), "Still #1 despite dampening"))

    impact = ""
    if have_impact and n_reco:
        impact = (
            f'<p class="note">Impact: dampening changed <b>{_fmt_int(flips)}</b> of '
            f"{_fmt_int(n_reco)} recommendations ({_fmt_pct(flips / n_reco, 1)}). "
            f"<b>{_fmt_int(still_top)}</b> dampened topic(s) were severe enough to remain the #1 pick "
            f"despite the x{_fmt_num(mult, 2)} penalty; the rest were pushed below another topic.</p>"
        )

    # Reasons: by-topic count + dampening rate (share of that topic's candidates).
    tot_by_topic = cand.groupby("topic").size()
    damp_by_topic = dd.groupby("topic").size()
    tbl = pd.DataFrame({"total": tot_by_topic, "damp": damp_by_topic}).fillna(0)
    tbl = tbl[tbl["damp"] > 0].sort_values("damp", ascending=False)
    max_d = int(tbl["damp"].max()) if len(tbl) else 0
    trows = []
    for topic, r in tbl.iterrows():
        dn, tn = int(r["damp"]), int(r["total"])
        trows.append(
            f'<tr class="barrow"><td>{_esc(topic)}</td>'
            f'<td class="num">{_fmt_int(dn)}</td>'
            f'<td class="num">{_fmt_pct(dn / tn if tn else 0, 1)}</td>'
            f'<td style="width:40%">{_bar(dn, max_d)}</td></tr>'
        )
    table = (
        '<div class="card"><table><thead><tr>'
        '<th>Topic</th><th class="num">Dampened</th><th class="num">Rate</th><th>Distribution</th>'
        f'</tr></thead><tbody>{"".join(trows)}</tbody></table></div>'
        '<p class="note">Rate = dampened share of that topic\'s candidates; a high rate means coaches '
        "recently worked that topic for many of those agents.</p>"
    )

    return (
        "<h2>Recency dampening</h2>" + mech
        + f'<div class="tiles">{"".join(tiles)}</div>'
        + impact + table
    )


def _data_issues_section(
    sig: pd.DataFrame, recs: pd.DataFrame,
    excluded: Optional[pd.DataFrame], candidates: Optional[pd.DataFrame],
) -> str:
    items = []

    # excluded signals by reason (or metric)
    if excluded is not None and not excluded.empty:
        key = "reason" if "reason" in excluded.columns else ("metric" if "metric" in excluded.columns else None)
        if key:
            vc = excluded[key].fillna("(unspecified)").value_counts().head(12)
            rows = "".join(
                f'<tr><td>{_esc(k)}</td><td class="num">{_fmt_int(v)}</td></tr>' for k, v in vc.items()
            )
            items.append(
                f'<div class="card" style="margin-bottom:12px"><table><thead><tr>'
                f'<th>Excluded signals by {_esc(key)}</th><th class="num">Count</th></tr></thead>'
                f'<tbody>{rows}</tbody></table></div>'
            )
    else:
        items.append('<p class="note">No signals were excluded by gating this run.</p>')

    # (dampening detail now lives in its own "Recency dampening" section)

    # coverage note
    if sig is not None and not sig.empty and "agent_id" in sig.columns:
        agents_with_signals = sig["agent_id"].astype(str).nunique()
        agents_reco = recs["agent_id"].nunique() if "agent_id" in recs.columns else 0
        gap = agents_with_signals - agents_reco
        if gap > 0:
            items.append(
                f'<p class="note">Coverage: {_fmt_int(agents_reco)} of {_fmt_int(agents_with_signals)} '
                f'agents with signals received a recommendation ({_fmt_int(gap)} had no eligible topic).</p>'
            )

    if not items:
        return ""
    return "<h2>Data issues &amp; diagnostics</h2>" + "".join(items)


def _page(body: str) -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Coaching Decision Engine — Run Dashboard</title>"
        f"<style>{_CSS}</style></head><body><div class='wrap'>{body}</div></body></html>"
    )


def write_dashboard(
    path: Path,
    recommendations: pd.DataFrame,
    signals: Optional[pd.DataFrame] = None,
    config: Optional[Dict[str, Any]] = None,
    excluded_signals: Optional[pd.DataFrame] = None,
    candidates: Optional[pd.DataFrame] = None,
    agents: Optional[pd.DataFrame] = None,
    generated_at: Optional[str] = None,
) -> Path:
    """Build and write dashboard.html. Never raises on content edge cases."""
    html_str = build_dashboard_html(
        recommendations=recommendations,
        signals=signals,
        config=config or {},
        excluded_signals=excluded_signals,
        candidates=candidates,
        agents=agents,
        generated_at=generated_at,
    )
    path = Path(path)
    path.write_text(html_str, encoding="utf-8")
    log.info("Wrote dashboard: %s", path)
    return path
