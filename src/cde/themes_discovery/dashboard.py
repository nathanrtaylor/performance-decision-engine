"""
Self-contained HTML dashboard for theme discovery: candidate themes with a per-theme guardrail
verdict + the co-movement evidence. Keeps its own copy of the CSS/helpers (mirrors
benchmarks_recalc.dashboard) so it stays self-contained.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from cde.reporting.dashboard_kit import esc as _esc, fmt_pct, chip, tile as _tile, page as _kit_page

from . import config as C
from .compare import CompareResult

# Discovery-only styling; shared tokens/typography/chip (incl. .chip .ico) live in
# dashboard_kit.BASE_CSS -- which also gives this dashboard the icon styling it previously lacked.
_EXTRA_CSS = """
.wrap{max-width:1180px}
h2{letter-spacing:0}
td{vertical-align:top} td.num,th.num{white-space:nowrap}
.card{padding:2px 2px;margin-top:8px}
.just{color:var(--text-2);font-size:12px} .mcell{font-weight:600}
.foot{margin-top:10px}
"""

_VERDICT_STYLE = {
    C.PROPOSE: ("warning", "⚠"),
    C.HOLD: ("serious", "▲"),
    C.SKIPPED: ("muted", "–"),
}


def _fmt_corr(x: Any) -> str:
    try:
        return f"{float(x):.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_pct(x: Any) -> str:
    return fmt_pct(x, digits=0)


def _chip(verdict: str) -> str:
    level, ico = _VERDICT_STYLE.get(verdict, ("muted", "•"))
    return chip(level, verdict, ico)


def _page(body: str) -> str:
    return _kit_page(body, title="Theme Discovery", css_extra=_EXTRA_CSS)


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
