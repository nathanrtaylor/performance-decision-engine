"""
Co-movement computation + clustering into candidate themes (no verdicts here).

Pipeline:
  1. compute_comovement: per cohort, correlate every metric pair on the bad axis (Pearson),
     recording the pairwise complete-observation count.
  2. cluster_into_themes: keep pairs that clear min_correlation in >= min_cohort_coverage of the
     cohorts where they had enough sample; take connected components as candidate themes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Dict, List, Tuple

import pandas as pd

from .config import DiscoveryThresholds


@dataclass(frozen=True)
class CorrPair:
    metric_a: str
    metric_b: str
    cohort: str
    corr: float
    n: int


@dataclass
class CandidateTheme:
    members: List[str]
    cohorts: List[str] = field(default_factory=list)   # cohorts where the theme's edges held
    mean_corr: float = 0.0
    coverage: float = 0.0                               # mean per-edge cohort coverage
    n_min: int = 0                                      # smallest supporting sample across edges
    edges: List[Tuple[str, str]] = field(default_factory=list)


def compute_comovement(
    matrices: Dict[str, pd.DataFrame], thr: DiscoveryThresholds
) -> List[CorrPair]:
    """Per cohort, Pearson-correlate every metric pair on the bad axis (min_periods = min_sample)."""
    pairs: List[CorrPair] = []
    for cohort in sorted(matrices):
        mat = matrices[cohort]
        if mat.shape[1] < 2:
            continue
        corr = mat.corr(min_periods=thr.min_sample)
        notna = mat.notna()
        metrics = list(mat.columns)
        for a, b in combinations(sorted(metrics), 2):
            c = corr.at[a, b]
            if pd.isna(c):
                continue
            n = int((notna[a] & notna[b]).sum())
            if n < thr.min_sample:
                continue
            pairs.append(CorrPair(a, b, cohort, float(c), n))
    return pairs


class _UnionFind:
    def __init__(self) -> None:
        self.parent: Dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def cluster_into_themes(
    pairs: List[CorrPair], thr: DiscoveryThresholds
) -> List[CandidateTheme]:
    """Keep qualifying edges (strong + consistent across cohorts); components => candidate themes."""
    # Aggregate per unordered metric pair across cohorts.
    by_pair: Dict[Tuple[str, str], List[CorrPair]] = {}
    for p in pairs:
        by_pair.setdefault((p.metric_a, p.metric_b), []).append(p)

    qualifying: Dict[Tuple[str, str], dict] = {}
    for pair, plist in by_pair.items():
        cohorts_with_sample = [p for p in plist if p.n >= thr.min_sample]
        if not cohorts_with_sample:
            continue
        strong = [p for p in cohorts_with_sample if p.corr >= thr.min_correlation]
        coverage = len(strong) / len(cohorts_with_sample)
        if coverage >= thr.min_cohort_coverage and strong:
            qualifying[pair] = {
                "cohorts": sorted(p.cohort for p in strong),
                "mean_corr": sum(p.corr for p in strong) / len(strong),
                "coverage": coverage,
                "n_min": min(p.n for p in strong),
            }

    if not qualifying:
        return []

    uf = _UnionFind()
    for a, b in qualifying:
        uf.union(a, b)

    # Group metrics by component root.
    comps: Dict[str, List[str]] = {}
    members_seen = set()
    for a, b in qualifying:
        members_seen.update((a, b))
    for m in sorted(members_seen):
        comps.setdefault(uf.find(m), []).append(m)

    themes: List[CandidateTheme] = []
    for root in sorted(comps):
        members = sorted(comps[root])
        if not (thr.min_theme_size <= len(members) <= thr.max_theme_size):
            continue
        edges = [pair for pair in qualifying if pair[0] in members and pair[1] in members]
        if not edges:
            continue
        cohorts = sorted({c for e in edges for c in qualifying[e]["cohorts"]})
        mean_corr = sum(qualifying[e]["mean_corr"] for e in edges) / len(edges)
        coverage = sum(qualifying[e]["coverage"] for e in edges) / len(edges)
        n_min = min(qualifying[e]["n_min"] for e in edges)
        themes.append(CandidateTheme(
            members=members, cohorts=cohorts, mean_corr=mean_corr,
            coverage=coverage, n_min=n_min, edges=sorted(edges),
        ))
    # Largest / strongest first (deterministic).
    themes.sort(key=lambda t: (-len(t.members), -t.mean_corr, t.members[0]))
    return themes
