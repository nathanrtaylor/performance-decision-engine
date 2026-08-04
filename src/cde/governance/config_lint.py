"""Config referential-integrity linter + raw-snapshot preflight.

A new metric currently has to be registered consistently across ~7-8 config files
(metric_catalog, topic_map, benchmarks, themes, coaching_history_map,
signal_thresholds, priorities). None of those cross-references is checked at load
time, so a typo surfaces downstream as *wrong or missing coaching* rather than an
error. This module validates every cross-reference once, in one place.

Design:
  * ERRORS are conditions that would produce wrong/absent coaching and that ALL
    current, valid configs already satisfy -- so adding the check is additive:
    today's configs pass, only future mistakes fail.
  * WARNINGS are inconsistencies that are tolerated by the engine at runtime
    (safe defaults) but are worth surfacing -- e.g. planned-but-disabled metrics,
    orphan topics that back the dampening vocabulary.

Reuses the single canonical loader ``resolve_active_config`` so the linter sees
exactly what the pipeline sees.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

VALID_DIRECTIONS = {"higher_is_better", "lower_is_better"}


@dataclass
class LintReport:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def ok(self) -> bool:
        return not self.errors

    def merge(self, other: "LintReport") -> "LintReport":
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        return self

    def render(self, *, strict: bool = False) -> str:
        lines: List[str] = []
        for w in self.warnings:
            lines.append(f"  WARN  {w}")
        for e in self.errors:
            lines.append(f"  ERROR {e}")
        if not lines:
            lines.append("  OK  no config integrity issues found")
        n_err = len(self.errors)
        n_warn = len(self.warnings)
        effective_fail = n_err or (strict and n_warn)
        status = "FAIL" if effective_fail else "PASS"
        lines.append(f"config-lint {status}: {n_err} error(s), {n_warn} warning(s)"
                     + (" [strict: warnings are errors]" if strict else ""))
        return "\n".join(lines)


def _inner(obj: Any, key: str) -> Dict[str, Any]:
    """Tolerate both {key: {...}} and already-unwrapped {...}."""
    if isinstance(obj, dict) and isinstance(obj.get(key), dict):
        return obj[key]
    return obj if isinstance(obj, dict) else {}


def lint_config(cfg: Dict[str, Any]) -> LintReport:
    """Validate cross-references in a resolved config (from resolve_active_config)."""
    r = LintReport()

    mc = _inner(cfg.get("metric_catalog") or {}, "metric_catalog")
    metrics: Dict[str, Any] = mc.get("metrics") or {}
    mnames = set(metrics)
    cats = set(mc.get("category_defaults") or {})

    tm = _inner(cfg.get("topic_map") or {}, "topic_map")
    m2t = tm.get("metric_to_topic") or {}
    t2c = tm.get("topic_to_conversation_type") or {}
    topics_defined = set(m2t.values())

    benchmarks = cfg.get("benchmarks") or {}
    themes = _inner(cfg.get("themes") or {}, "themes")
    chm = _inner(cfg.get("coaching_history_map") or {}, "coaching_history_map").get("behavior_to_topic") or {}
    thr = _inner(cfg.get("thresholds") or {}, "signal_thresholds")
    by_metric = thr.get("by_metric") or {}
    tie_order = (cfg.get("tie_breakers") or {}).get("topic_order") or []

    if not metrics:
        r.errors.append("metric_catalog: no metrics found (catalog missing or malformed)")
        return r

    # Governance block declares what the catalog promises to enforce. Previously most of
    # these flags were decorative (declared but never read); the linter now honors them so
    # they are real. The two disallow_* flags are enforced at runtime (build_signals,
    # prioritization/apply) and are not re-checked here. require_direction/require_category
    # default True to preserve the linter's original always-on behavior; the field-presence
    # flags default False so they only bite when explicitly declared.
    gov = mc.get("governance") or {}
    require_direction = bool(gov.get("require_direction", True))
    require_category = bool(gov.get("require_category", True))
    require_unit = bool(gov.get("require_unit", False))
    require_source = bool(gov.get("require_source", False))
    require_source_key = bool(gov.get("require_source_metric_key", False))

    # ---- ERRORS (all currently-valid configs already satisfy these) ----
    for n, d in metrics.items():
        direction = d.get("direction")
        if require_direction and direction not in VALID_DIRECTIONS:
            # Closes the silent wrong-direction path: a blank/misspelled direction is
            # otherwise treated as lower_is_better at signals/thresholds.py, inverting
            # the deficit and coaching the wrong tail.
            r.errors.append(
                f"metric_catalog: '{n}' has invalid direction {direction!r} "
                f"(must be one of {sorted(VALID_DIRECTIONS)})"
            )
        category = d.get("category")
        if require_category and category not in cats:
            r.errors.append(
                f"metric_catalog: '{n}' category {category!r} not in category_defaults {sorted(cats)}"
            )
        if require_unit and not d.get("unit"):
            r.errors.append(f"metric_catalog: '{n}' is missing required field 'unit' (governance.require_unit)")
        if require_source and not d.get("source"):
            r.errors.append(f"metric_catalog: '{n}' is missing required field 'source' (governance.require_source)")
        if require_source_key and not d.get("source_metric_key"):
            r.errors.append(
                f"metric_catalog: '{n}' is missing required field 'source_metric_key' "
                f"(governance.require_source_metric_key)"
            )
        bench = d.get("benchmark") or {}
        if bench.get("type") == "config" and n not in benchmarks:
            r.errors.append(
                f"benchmarks: metric '{n}' uses benchmark.type=config but has no entry in benchmarks.yaml"
            )
        if d.get("eligible_for_prioritization") and n not in m2t:
            r.errors.append(
                f"topic_map: eligible metric '{n}' has no topic in metric_to_topic "
                f"(it can never be recommended)"
            )

    for tn, tb in (themes or {}).items():
        for mem in ((tb or {}).get("members") or []):
            if mem not in mnames:
                r.errors.append(f"themes: theme '{tn}' member '{mem}' is not a metric in metric_catalog")

    for k in by_metric:
        if k not in mnames:
            r.errors.append(f"signal_thresholds.by_metric: '{k}' is not a metric in metric_catalog")

    # ---- WARNINGS (tolerated at runtime; surfaced for review) ----
    for m in m2t:
        if m not in mnames:
            r.warnings.append(
                f"topic_map: metric '{m}' is mapped to a topic but not defined in metric_catalog "
                f"(planned/disabled source?)"
            )
    for beh, topic in chm.items():
        if topic not in topics_defined:
            r.warnings.append(
                f"coaching_history_map: '{beh}' -> topic '{topic}' which no metric maps to (will not dampen)"
            )
    for t in tie_order:
        if t not in topics_defined:
            r.warnings.append(f"tie_breakers.topic_order: '{t}' is not a topic any metric maps to (ignored)")
    for t in t2c:
        if t not in topics_defined:
            r.warnings.append(f"topic_map.topic_to_conversation_type: '{t}' has no metric mapping (orphan topic)")
    for k in benchmarks:
        if k not in mnames:
            r.warnings.append(f"benchmarks: entry '{k}' does not correspond to a metric in metric_catalog (stale?)")

    return r


def preflight_snapshot(raw_dir: Path, config: Dict[str, Any]) -> LintReport:
    """Cheap pre-run checks on the raw snapshot (folds README Appendix A #1 and #5).

    Data-quality issues are WARNINGS (the run can still proceed); a missing/empty
    required table is an ERROR (the run would fail or silently under-cover).
    """
    import pandas as pd

    r = LintReport()
    raw_dir = Path(raw_dir)
    required = config.get("required_tables") or []
    for t in required:
        p = raw_dir / f"{t}.csv"
        if not p.exists():
            r.errors.append(f"snapshot: required table '{t}' missing at {p}")
            continue
        try:
            df = pd.read_csv(p)
        except Exception as e:  # noqa: BLE001
            r.errors.append(f"snapshot: failed to read {p}: {e!r}")
            continue
        if df.empty:
            r.errors.append(f"snapshot: required table '{t}' is empty ({p})")

    am = raw_dir / "agent_metrics.csv"
    if am.exists():
        try:
            df = pd.read_csv(am)
        except Exception:  # noqa: BLE001
            df = None
        if df is not None and not df.empty:
            if "calc" in df.columns:
                null_pct = float(df["calc"].isna().mean())
                if null_pct > 0.5:
                    r.warnings.append(f"snapshot: agent_metrics.calc is {null_pct:.0%} null")
            if "denominator" in df.columns:
                zero_pct = float((pd.to_numeric(df["denominator"], errors="coerce") == 0).mean())
                if zero_pct > 0.5:
                    r.warnings.append(f"snapshot: agent_metrics.denominator is {zero_pct:.0%} zero")
            period_col = "week_ending" if "week_ending" in df.columns else (
                "period" if "period" in df.columns else None
            )
            if period_col is not None:
                s = pd.to_datetime(df[period_col], errors="coerce")
                weekdays = s.dt.day_name().dropna().nunique()
                if weekdays > 1:
                    r.warnings.append(
                        f"snapshot: agent_metrics.{period_col} spans {weekdays} weekdays "
                        f"(weekly periods should align to one weekday)"
                    )
    return r
