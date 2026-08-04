"""
Self-contained HTML dashboard: OLD vs NEW benchmarks with a per-metric guardrail verdict.

Mirrors the style of cde.reporting.dashboard (inline CSS, light+dark, no external deps) but keeps its
own copy of the tokens/helpers so it stays self-contained and does not couple to that module's
underscore-private internals.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from cde.reporting.dashboard_kit import esc as _esc, fmt_num, fmt_pct, chip, tile as _tile, page as _kit_page

from . import config as C
from .compare import BenchmarkDiffRow, CompareResult

# Recalc-only styling; shared tokens/typography/chip live in dashboard_kit.BASE_CSS.
_EXTRA_CSS = """
.wrap{max-width:1180px}
td{vertical-align:top} td.num,th.num{white-space:nowrap}
.card{padding:2px 2px;margin-top:8px}
.just{color:var(--text-2);font-size:12px} .mcell{font-weight:600}
.foot{margin-top:10px}
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


def _fmt_num(x: Any, digits: int = 4) -> str:
    return fmt_num(x, digits)


def _fmt_pct(x: Any) -> str:
    return fmt_pct(x, digits=1, signed=True)


def _fmt_delta(x: Any) -> str:
    try:
        if x is None:
            return "—"
        return f"{float(x):+.4g}"
    except (TypeError, ValueError):
        return "—"


def _chip(verdict: str) -> str:
    level, ico = _VERDICT_STYLE.get(verdict, ("muted", "•"))
    return chip(level, verdict, ico)


def _page(body: str) -> str:
    return _kit_page(body, title="Benchmark Recalculation", css_extra=_EXTRA_CSS)


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
