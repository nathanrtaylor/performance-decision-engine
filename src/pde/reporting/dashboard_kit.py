"""Shared HTML dashboard kit.

One home for the primitives that three dashboards had each copy-pasted with drift:
the run dashboard (``reporting.dashboard``) and the two propose-only dashboards
(``benchmarks_recalc.dashboard``, ``themes_discovery.dashboard``).

Exposed pieces:
  - ``BASE_CSS``          : the shared palette + typography + tiles + table + chip styling.
  - ``esc`` / ``fmt_*``   : escaping + number/percent formatters.
  - ``chip`` / ``tile``   : the two shared inline components.
  - ``page``              : the ``<!doctype>`` shell (title + CSS + body wrap).

Each dashboard passes its section-specific CSS via ``page(..., css_extra=...)`` and keeps
its own domain maps (verdict styles, category titles). Consolidating this also fixes the
themes dashboard's missing ``.chip .ico`` styling — the shared ``BASE_CSS`` carries it.
"""
from __future__ import annotations

import html
from typing import Any

import pandas as pd

# "no value" sentinel, shared so every table renders the same em-dash.
DASH = "—"

# Level -> status icon. Levels beyond a given dashboard's palette are simply unused.
ICONS = {"good": "✓", "warning": "⚠", "serious": "▲", "critical": "✕", "muted": "–"}

# Shared core styling. Section-specific rules (bars, justification columns, wider tables)
# are appended per dashboard via ``page(css_extra=...)``; later rules override earlier ones.
BASE_CSS = """
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
.wrap{max-width:1120px;margin:0 auto;padding:28px 22px 64px}
h1{font-size:22px;margin:0 0 2px} h2{font-size:15px;margin:34px 0 10px;letter-spacing:.01em}
.sub{color:var(--text-2);font-size:12.5px;margin:0 0 4px}
.note{color:var(--muted);font-size:12px;margin:6px 2px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:16px}
.tile{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:14px 16px}
.tile .v{font-size:26px;font-weight:650} .tile .k{color:var(--text-2);font-size:12px;margin-top:2px}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:6px 4px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--grid);vertical-align:middle}
th{color:var(--muted);font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.03em}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
.chip{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:600;
  padding:2px 9px;border-radius:999px;border:1px solid var(--border);white-space:nowrap}
.chip .ico{font-size:11px;line-height:1}
.chip.good{color:var(--good)} .chip.warning{color:#8a6100} .chip.serious{color:#a2432a} .chip.critical{color:var(--critical)}
.chip.muted{color:var(--muted)}
@media (prefers-color-scheme:dark){.chip.warning{color:var(--warning)} .chip.serious{color:var(--serious)}}
.chip.good .ico{color:var(--good)} .chip.warning .ico{color:var(--warning)}
.chip.serious .ico{color:var(--serious)} .chip.critical .ico{color:var(--critical)}
.foot{color:var(--muted);font-size:11.5px;margin-top:8px}
"""


def esc(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def fmt_int(x: Any) -> str:
    try:
        return f"{int(round(float(x))):,}"
    except (TypeError, ValueError):
        return DASH


def fmt_pct(x: Any, digits: int = 0, signed: bool = False) -> str:
    """Percent of a 0-1 fraction. ``signed`` shows an explicit +/- (for deltas)."""
    try:
        v = float(x) * 100
        return f"{v:+.{digits}f}%" if signed else f"{v:.{digits}f}%"
    except (TypeError, ValueError):
        return DASH


def fmt_num(x: Any, digits: int = 3) -> str:
    try:
        v = float(x)
        return DASH if pd.isna(v) else f"{v:.{digits}g}"
    except (TypeError, ValueError):
        return DASH


def chip(level: str, label: str, ico: Any = None) -> str:
    """Status pill. ``ico`` defaults to the icon for ``level``; pass explicitly to override."""
    glyph = ICONS.get(level, "") if ico is None else ico
    return f'<span class="chip {level}"><span class="ico">{esc(glyph)}</span>{esc(label)}</span>'


def tile(value: str, key: str) -> str:
    return f'<div class="tile"><div class="v">{value}</div><div class="k">{esc(key)}</div></div>'


def page(body: str, *, title: str, css_extra: str = "") -> str:
    """The full HTML document shell: ``title`` + ``BASE_CSS`` (+ optional ``css_extra``) + body."""
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{esc(title)}</title>"
        f"<style>{BASE_CSS}{css_extra}</style></head><body>"
        f"<div class='wrap'>{body}</div></body></html>"
    )
