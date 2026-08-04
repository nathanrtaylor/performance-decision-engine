"""
Self-contained HTML dashboard for theme discovery: candidate themes with a per-theme guardrail
verdict + the co-movement evidence. Keeps its own copy of the CSS/helpers (mirrors
benchmarks_recalc.dashboard) so it stays self-contained.
"""
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import config as C
from .compare import CompareResult

_CSS = """
:root{
  --surface-1:#fcfcfb; --page:#f9f9f7; --text-1:#0b0b0b; --text-2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --border:rgba(11,11,11,.10);
  --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
}
@media (prefers-color-scheme:dark){:root{
  --surface-1:#1a1a19; --page:#0d0d0d; --text-1:#fff; --text-2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --border:rgba(255,255,255,.10);
}}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--text-1);
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif;font-size:14px;line-height:1.45}
.wrap{max-width:1180px;margin:0 auto;padding:28px 22px 64px}
h1{font-size:22px;margin:0 0 2px} h2{font-size:15px;margin:34px 0 10px}
.sub{color:var(--text-2);font-size:12.5px;margin:0 0 4px}
.note{color:var(--muted);font-size:12px;margin:6px 2px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-top:16px}
.tile{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:14px 16px}
.tile .v{font-size:26px;font-weight:650} .tile .k{color:var(--text-2);font-size:12px;margin-top:2px}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:2px 2px;margin-top:8px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--grid);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.03em}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr:last-child td{border-bottom:none}
.chip{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:600;
  padding:2px 9px;border-radius:999px;border:1px solid var(--border);white-space:nowrap}
.chip.good{color:var(--good)} .chip.warning{color:#8a6100} .chip.serious{color:#a2432a}
.chip.muted{color:var(--muted)}
@media (prefers-color-scheme:dark){.chip.warning{color:var(--warning)} .chip.serious{color:var(--serious)}}
.just{color:var(--text-2);font-size:12px} .mcell{font-weight:600}
.foot{color:var(--muted);font-size:11.5px;margin-top:10px}
"""

_VERDICT_STYLE = {
    C.PROPOSE: ("warning", "⚠"),
    C.HOLD: ("serious", "▲"),
    C.SKIPPED: ("muted", "–"),
}


def _esc(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _fmt_corr(x: Any) -> str:
    try:
        return f"{float(x):.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_pct(x: Any) -> str:
    try:
        return f"{float(x) * 100:.0f}%"
    except (TypeError, ValueError):
        return "—"


def _chip(verdict: str) -> str:
    level, ico = _VERDICT_STYLE.get(verdict, ("muted", "•"))
    return f'<span class="chip {level}"><span class="ico">{ico}</span>{_esc(verdict)}</span>'


def _tile(value: str, key: str) -> str:
    return f'<div class="tile"><div class="v">{value}</div><div class="k">{_esc(key)}</div></div>'


def _page(body: str) -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Theme Discovery</title>"
        f"<style>{_CSS}</style></head><body><div class='wrap'>{body}</div></body></html>"
    )


def build_discovery_dashboard_html(
    result: CompareResult, meta: dict, generated_at: Optional[str] = None
) -> str:
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    counts = result.counts

    parts: list[str] = []
    parts.append(
        "<h1>Theme Discovery — Candidate Coaching Themes</h1>"
        f'<p class="sub">Snapshot <b>{_esc(meta.get("snapshot", "-"))}</b> · '
        f'window {_esc(meta.get("window_weeks", "-"))} weeks · generated {_esc(generated_at)}</p>'
        '<p class="note">Propose-only. Candidates are groups of metrics that <b>move together</b> on a '
        "direction-adjusted (higher = worse) axis across agents, within ICP_Client cohorts. "
        "<b>PROPOSE</b> cleared every guardrail; <b>HOLD</b> = weak/inconsistent co-movement; "
        "<b>SKIPPED</b> = insufficient sample. A human SME confirms and names each theme in themes.yaml — "
        "discovery never adds themes automatically.</p>"
    )

    tiles = [
        _tile(str(counts.get(C.PROPOSE, 0)), "Proposed themes"),
        _tile(str(counts.get(C.HOLD, 0)), "Held (guardrail)"),
        _tile(str(counts.get(C.SKIPPED, 0)), "Skipped"),
        _tile(str(len(result.rows)), "Candidates analyzed"),
        _tile(_esc(meta.get("snapshot", "-")), "Data snapshot"),
    ]
    parts.append(f'<div class="tiles">{"".join(tiles)}</div>')

    head = (
        "<thead><tr><th>Candidate</th><th>Members</th><th class='num'>Mean r</th>"
        "<th class='num'>Coverage</th><th class='num'>n≥</th><th>Cohorts</th>"
        "<th>Verdict</th><th>Justification</th></tr></thead>"
    )
    rows_html = []
    for i, r in enumerate(result.rows, 1):
        tag = "new" if r.is_new else f"~ {r.matched_theme}"
        rows_html.append(
            "<tr>"
            f'<td class="mcell">Candidate {i}<br><span class="just">{_esc(tag)}</span></td>'
            f"<td>{_esc(', '.join(r.members))}</td>"
            f'<td class="num">{_fmt_corr(r.mean_corr)}</td>'
            f'<td class="num">{_fmt_pct(r.coverage)}</td>'
            f'<td class="num">{_esc(r.n_min)}</td>'
            f"<td>{_esc(', '.join(r.cohorts))}</td>"
            f"<td>{_chip(r.verdict)}</td>"
            f'<td class="just">{_esc(r.justification)}</td>'
            "</tr>"
        )
    if not rows_html:
        rows_html.append('<tr><td colspan="8" class="note">No candidate themes were found.</td></tr>')
    parts.append(f'<h2>Candidate themes</h2><div class="card"><table>{head}<tbody>{"".join(rows_html)}</tbody></table></div>')

    parts.append(
        '<p class="foot">Co-movement = Pearson correlation of 8-week windowed means across agents, '
        "on a direction-adjusted (higher = worse) axis, computed per ICP_Client cohort. Guardrails: "
        "sample sufficiency, correlation strength, cohort coverage, theme-size sanity.</p>"
    )
    return _page("".join(parts))


def write_discovery_dashboard(
    path: Path, result: CompareResult, meta: dict, generated_at: Optional[str] = None
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_discovery_dashboard_html(result, meta, generated_at), encoding="utf-8")
    return path
