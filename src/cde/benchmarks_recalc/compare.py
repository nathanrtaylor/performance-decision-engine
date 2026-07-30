"""
Compare recomputed candidates against the current benchmarks and attach a guardrail verdict.

The "current" value for a cohort is its EFFECTIVE current benchmark (what scoring uses today for that
cohort): reuses signals.benchmarks.get_benchmark_value, so a cohort with no explicit entry correctly
compares against the metric's default. Emits one row per (metric, cohort).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from cde.signals.benchmarks import get_benchmark_value

from . import config as C
from .config import RecalcThresholds
from .guardrails import evaluate
from .prep import PreppedFrames
from .recompute import CandidateBenchmark, CohortStat


@dataclass(frozen=True)
class BenchmarkDiffRow:
    metric: str
    category: str
    cohort: str                       # 'default' | icp_client
    old: Optional[float]
    new: Optional[float]
    delta: Optional[float]
    pct_change: Optional[float]
    verdict: str                      # PROPOSE | HOLD | UNCHANGED | SKIPPED
    justification: str
    is_new_cohort: bool = False
    checks: Dict[str, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class CompareResult:
    rows: List[BenchmarkDiffRow]
    counts: Dict[str, int]

    def by_category(self) -> Dict[str, List[BenchmarkDiffRow]]:
        out: Dict[str, List[BenchmarkDiffRow]] = {}
        for r in self.rows:
            out.setdefault(r.category, []).append(r)
        return out


def read_current_benchmarks(config: Dict[str, Any]) -> Dict[str, Any]:
    return config.get("benchmarks") or {}


def _current_value(benchmarks: Dict[str, Any], benchmark_key: str, cohort: Optional[str]) -> Optional[float]:
    icp = None if (cohort in (None, "default")) else cohort
    return get_benchmark_value(benchmark_key, None, {"benchmarks": benchmarks}, icp_client=icp)


def _explicit_cohort_keys(benchmarks: Dict[str, Any], benchmark_key: str) -> set:
    entry = benchmarks.get(benchmark_key)
    if isinstance(entry, dict):
        return {str(k).strip().lower() for k in (entry.get("by_icp_client") or {})}
    return set()


def _row(
    metric: str,
    category: str,
    benchmark_key: str,
    stat: CohortStat,
    cand: CandidateBenchmark,
    benchmarks: Dict[str, Any],
    thr: RecalcThresholds,
    explicit_cohorts: set,
) -> BenchmarkDiffRow:
    cohort = stat.cohort
    old = _current_value(benchmarks, benchmark_key, cohort)
    new = stat.value
    delta = (new - old) if (new is not None and old is not None) else None
    pct = (delta / old) if (delta is not None and old not in (None, 0)) else None
    report = evaluate(stat, old, category, cand.value_lo, cand.value_hi, thr)
    is_new = cohort != "default" and cohort not in explicit_cohorts
    justification = report.reason + (" [new cohort split]" if (is_new and report.verdict == C.PROPOSE) else "")
    return BenchmarkDiffRow(
        metric=metric, category=category, cohort=cohort, old=old, new=new,
        delta=delta, pct_change=pct, verdict=report.verdict, justification=justification,
        is_new_cohort=is_new, checks=report.checks,
    )


def compare(
    candidates: Dict[str, CandidateBenchmark],
    prepped: PreppedFrames,
    config: Dict[str, Any],
    thr: RecalcThresholds,
) -> CompareResult:
    benchmarks = read_current_benchmarks(config)
    rows: List[BenchmarkDiffRow] = []

    for metric, cand in candidates.items():
        bkey = prepped.metric_meta[metric].benchmark_key if metric in prepped.metric_meta else metric
        explicit = _explicit_cohort_keys(benchmarks, bkey)

        if cand.skipped:
            old = _current_value(benchmarks, bkey, "default")
            rows.append(BenchmarkDiffRow(
                metric=metric, category=cand.category, cohort="default", old=old, new=old,
                delta=0.0 if old is not None else None, pct_change=0.0 if old else None,
                verdict=C.SKIPPED, justification=cand.default.note,
            ))
            continue

        rows.append(_row(metric, cand.category, bkey, cand.default, cand, benchmarks, thr, explicit))
        for cohort in sorted(cand.by_icp_client):
            rows.append(_row(metric, cand.category, bkey, cand.by_icp_client[cohort], cand, benchmarks, thr, explicit))

    counts = {C.PROPOSE: 0, C.HOLD: 0, C.UNCHANGED: 0, C.SKIPPED: 0}
    for r in rows:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    return CompareResult(rows=rows, counts=counts)
