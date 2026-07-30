"""
Self-contained HTML dashboard: OLD vs NEW benchmarks with a per-metric guardrail verdict.

Mirrors the style of cde.reporting.dashboard (inline CSS, light+dark, no external deps) but keeps its
own copy of the tokens/helpers so it stays self-contained and does not couple to that module's
underscore-private internals.
"""
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import config as C
from .compare import BenchmarkDiffRow, CompareResult

_CSS = """
:root{
  --surface-1:#fcfcfb; --page:#f9f9f7; --text-1:#0b0b0b; --text-2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --border:rgba(11,11,11,.10); --series:#2a78d6; --track:#eef1f5;
  --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
}
@media (prefers-color-scheme:dark){:root{
  --surface-1:#1a1a19; --page:#0d0d0d; --text-1:#fff; --text-2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --border:rgba(255,255,255,.10); --series:#3987e5; --track:#242422;
}}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--text-1);
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif;font-size:14px;line-height:1.45}
.wrap{max-width:1180px;margin:0 auto;padding:28px 22px 64px}
h1{font-size:22px;margin:0 0 2px} h2{font-size:15px;margin:34px 0 10px;letter-spacing:.01em}
.sub{color:var(--text-2);font-size:12.5px;margin:0 0 4px}
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
.chip .ico{font-size:11px;line-height:1}
.chip.good{color:var(--good)} .chip.warning{color:#8a6100} .chip.serious{color:#a2432a}
.chip.muted{color:var(--muted)}
@media (prefers-color-scheme:dark){.chip.warning{color:var(--warning)} .chip.serious{color:var(--serious)}}
.just{color:var(--text-2);font-size:12px}
.mcell{font-weight:600}
.foot{color:var(--muted);font-size:11.5px;margin-top:10px}
.note{color:var(--muted);font-size:12px;margin:6px 2px 0}
"""

# verdict -> (chip level, icon)
_VERDICT_STYLE = {
    C.PROPOSE: ("warning", "⚠"),
    C.UNCHANGED: ("good", "✓"),
    C.HOLD: ("serious", "▲"),
    C.SKIPPED: ("muted", "–"),
}

_CATEGORY_TITLES = [
    (C.CAT_OPERATIONAL, "Operational metrics (per-cohort medians)"),
    (C.CAT_SALES, "Sales (nsp100)"),
    (C.CAT_ABSOLUTE, "Absolute-default metrics"),
    (C.CAT_QUALITY, "Quality behaviors (p25 floor)"),
    (C.CAT_SENTIMENT, "Sentiment behaviors (p25 floor, Verizon-only)"),
    (C.CAT_TOOL, "Tool-usage (skipped — sources inactive)"),
]


def _esc(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _fmt_num(x: Any, digits: int = 4) -> str:
    try:
        if x is None:
            return "—"
        v = float(x)
        return f"{v:.{digits}g}"
    except (TypeError, ValueError):
        return "—"


def _fmt_pct(x: Any) -> str:
    try:
        if x is None:
            return "—"
        return f"{float(x) * 100:+.1f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_delta(x: Any) -> str:
    try:
        if x is None:
            return "—"
        return f"{float(x):+.4g}"
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
        "<title>Benchmark Recalculation</title>"
        f"<style>{_CSS}</style></head><body><div class='wrap'>{body}</div></body></html>"
    )


def _row_html(r: BenchmarkDiffRow) -> str:
    metric_cell = f'<span class="mcell">{_esc(r.metric)}</span>' if r.cohort == "default" else ""
    return (
        "<tr>"
        f"<td>{metric_cell}</td>"
        f"<td>{_esc(r.cohort)}</td>"
        f'<td class="num">{_fmt_num(r.old)}</td>'
        f'<td class="num">{_fmt_num(r.new)}</td>'
        f'<td class="num">{_fmt_delta(r.delta)}</td>'
        f'<td class="num">{_fmt_pct(r.pct_change)}</td>'
        f"<td>{_chip(r.verdict)}</td>"
        f'<td class="just">{_esc(r.justification)}</td>'
        "</tr>"
    )


def _category_table(rows: list[BenchmarkDiffRow]) -> str:
    head = (
        "<thead><tr><th>Metric</th><th>Cohort</th><th class='num'>Old</th><th class='num'>New</th>"
        "<th class='num'>Δ</th><th class='num'>%Δ</th><th>Verdict</th><th>Justification</th></tr></thead>"
    )
    # group rows by metric, default first (already ordered by compare)
    body = "".join(_row_html(r) for r in rows)
    return f'<div class="card"><table>{head}<tbody>{body}</tbody></table></div>'


def build_recalc_dashboard_html(
    result: CompareResult, meta: dict, generated_at: Optional[str] = None
) -> str:
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    by_cat = result.by_category()
    counts = result.counts

    parts: list[str] = []
    parts.append(
        "<h1>Benchmark Recalculation — Proposed vs Current</h1>"
        f'<p class="sub">Snapshot <b>{_esc(meta.get("snapshot", "-"))}</b> · '
        f'window {_esc(meta.get("window_weeks", "-"))} weeks · generated {_esc(generated_at)}</p>'
        '<p class="note">Propose-only. Rows marked <b>PROPOSE</b> cleared every guardrail and moved '
        "materially; <b>HOLD</b> = evidence insufficient / degenerate / out-of-range; "
        "<b>UNCHANGED</b> = within materiality threshold; <b>SKIPPED</b> = source inactive. "
        "No benchmark is changed without an explicit authorized apply.</p>"
    )

    tiles = [
        _tile(str(counts.get(C.PROPOSE, 0)), "Proposed changes"),
        _tile(str(counts.get(C.HOLD, 0)), "Held (guardrail)"),
        _tile(str(counts.get(C.UNCHANGED, 0)), "Unchanged"),
        _tile(str(counts.get(C.SKIPPED, 0)), "Skipped"),
        _tile(str(len({r.metric for r in result.rows})), "Metrics analyzed"),
        _tile(_esc(meta.get("snapshot", "-")), "Data snapshot"),
    ]
    parts.append(f'<div class="tiles">{"".join(tiles)}</div>')

    for cat_key, title in _CATEGORY_TITLES:
        rows = by_cat.get(cat_key)
        if not rows:
            continue
        n_prop = sum(1 for r in rows if r.verdict == C.PROPOSE)
        parts.append(f"<h2>{_esc(title)} — {n_prop} proposed</h2>")
        parts.append(_category_table(rows))

    parts.append(
        '<p class="foot">Anchors: operational = per-cohort median of 8-week windowed means; '
        "behaviors = p25 of windowed means capped at 0.95. Guardrails: sample sufficiency, "
        "materiality, non-degeneracy, cohort-split validity, observed-range sanity.</p>"
    )
    return _page("".join(parts))


def write_recalc_dashboard(
    path: Path, result: CompareResult, meta: dict, generated_at: Optional[str] = None
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_recalc_dashboard_html(result, meta, generated_at), encoding="utf-8")
    return path
