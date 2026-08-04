"""Tests for the propose-only theme discovery module (src/cde/themes_discovery)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cde.themes_discovery import config as C
from cde.themes_discovery.apply import apply_themes
from cde.themes_discovery.compare import CompareResult, ThemeProposalRow, compare
from cde.themes_discovery.config import DiscoveryThresholds
from cde.themes_discovery.dashboard import build_discovery_dashboard_html
from cde.themes_discovery.guardrails import evaluate
from cde.themes_discovery.prep import RawFrames, build_bad_axis_by_cohort, prep_frames
from cde.themes_discovery.recompute import (
    CandidateTheme, CorrPair, cluster_into_themes, compute_comovement,
)

WEEKS = [
    "2026-06-05", "2026-06-12", "2026-06-19", "2026-06-26",
    "2026-07-03", "2026-07-10", "2026-07-17", "2026-07-24",
]

THR = DiscoveryThresholds(min_sample=10, min_correlation=0.4, min_cohort_coverage=0.5)


# ---------------------------------------------------------------------------
# synthetic extract builders (mirror test_benchmarks_recalc)
# ---------------------------------------------------------------------------
def _am_rows(metric_raw, cohort, agents_calc, denom=100.0):
    rows = []
    for agent_id, calc in agents_calc:
        for wk in WEEKS:
            rows.append(dict(agent_id=agent_id, week_ending=wk, call_type="all", metric=metric_raw,
                             icp_client=cohort, site="t", numerator=calc * denom, denominator=denom, calc=calc))
    return rows


def _op_meta(name, raw, direction="lower_is_better"):
    return {"source": "agent_metrics", "source_metric_key": raw, "category": "business",
            "direction": direction, "unit": "rate", "benchmark": {"type": "config", "key": name}}


def _config(metrics: dict, themes: dict | None = None) -> dict:
    cfg = {"metric_catalog": {"metric_catalog": {"metrics": metrics}}}
    if themes is not None:
        cfg["themes"] = {"themes": themes}
    return cfg


def _raw(agent_metrics=None):
    return RawFrames(agents=pd.DataFrame(), agent_metrics=pd.DataFrame(agent_metrics or []),
                     behavior_scores=pd.DataFrame(), raw_dir=None, snapshot_id="test-snap")


# ---------------------------------------------------------------------------
# co-movement + clustering
# ---------------------------------------------------------------------------
def test_correlated_metrics_form_a_candidate_theme():
    # m_a (lower_is_better) and m_b (higher_is_better) driven by the same latent "badness" per agent.
    rng = np.random.default_rng(0)
    n = 40
    latent = rng.normal(size=n)
    a_vals = 0.5 + 0.1 * latent           # lower_is_better: higher = worse -> tracks +latent
    b_vals = 0.8 - 0.1 * latent           # higher_is_better: lower = worse -> -b tracks +latent
    am = []
    for i in range(n):
        am += _am_rows("a raw", "mob-verizon", [(f"g{i}", a_vals[i])])
        am += _am_rows("b raw", "mob-verizon", [(f"g{i}", b_vals[i])])
    cfg = _config({"m_a": _op_meta("m_a", "a raw", "lower_is_better"),
                   "m_b": _op_meta("m_b", "b raw", "higher_is_better")})
    prepped = prep_frames(_raw(am), cfg)
    matrices = build_bad_axis_by_cohort(prepped, cfg)
    assert "mob-verizon" in matrices
    pairs = compute_comovement(matrices, THR)
    themes = cluster_into_themes(pairs, THR)
    assert len(themes) == 1
    assert set(themes[0].members) == {"m_a", "m_b"}
    assert themes[0].mean_corr >= 0.4


def test_uncorrelated_metrics_do_not_cluster():
    rng = np.random.default_rng(1)
    n = 40
    am = []
    for i in range(n):
        am += _am_rows("a raw", "mob-verizon", [(f"g{i}", rng.normal())])
        am += _am_rows("b raw", "mob-verizon", [(f"g{i}", rng.normal())])
    cfg = _config({"m_a": _op_meta("m_a", "a raw"), "m_b": _op_meta("m_b", "b raw")})
    prepped = prep_frames(_raw(am), cfg)
    matrices = build_bad_axis_by_cohort(prepped, cfg)
    themes = cluster_into_themes(compute_comovement(matrices, THR), THR)
    assert themes == []


# ---------------------------------------------------------------------------
# guardrails
# ---------------------------------------------------------------------------
def test_guardrail_propose_hold_skip():
    ok = CandidateTheme(members=["a", "b"], cohorts=["c1"], mean_corr=0.6, coverage=1.0, n_min=30)
    assert evaluate(ok, THR).verdict == C.PROPOSE

    weak = CandidateTheme(members=["a", "b"], cohorts=["c1"], mean_corr=0.2, coverage=1.0, n_min=30)
    assert evaluate(weak, THR).verdict == C.HOLD

    thin = CandidateTheme(members=["a", "b"], cohorts=["c1"], mean_corr=0.9, coverage=1.0, n_min=3)
    assert evaluate(thin, THR).verdict == C.SKIPPED


# ---------------------------------------------------------------------------
# compare vs existing themes
# ---------------------------------------------------------------------------
def test_compare_flags_new_vs_existing():
    themes = [
        CandidateTheme(members=["m_a", "m_b"], cohorts=["c1"], mean_corr=0.6, coverage=1.0, n_min=30),
        CandidateTheme(members=["x1", "x2", "x3"], cohorts=["c1"], mean_corr=0.7, coverage=1.0, n_min=30),
    ]
    cfg = _config({}, themes={"Known": {"members": ["m_a", "m_b"], "conversation_type": "Q"}})
    result = compare(themes, cfg, THR)
    by_members = {tuple(r.members): r for r in result.rows}
    assert by_members[("m_a", "m_b")].is_new is False
    assert by_members[("m_a", "m_b")].matched_theme == "Known"
    assert by_members[("x1", "x2", "x3")].is_new is True
    assert result.counts[C.PROPOSE] == 2


# ---------------------------------------------------------------------------
# dashboard + apply (propose-only, human-merge)
# ---------------------------------------------------------------------------
def _result_one_propose():
    row = ThemeProposalRow(members=["m_a", "m_b"], cohorts=["mob-verizon"], mean_corr=0.6,
                           coverage=1.0, n_min=30, verdict=C.PROPOSE, justification="j", is_new=True)
    return CompareResult(rows=[row], counts={C.PROPOSE: 1, C.HOLD: 0, C.SKIPPED: 0})


def test_dashboard_renders():
    html = build_discovery_dashboard_html(_result_one_propose(), {"snapshot": "s", "window_weeks": 8})
    assert html.startswith("<!doctype html")
    assert "Candidate themes" in html
    assert "PROPOSE" in html
    assert "m_a" in html


def test_apply_defers_and_never_edits_themes(tmp_path):
    themes_yaml = tmp_path / "themes.yaml"
    original = "themes:\n  Known:\n    members: [a, b]\n    conversation_type: Q\n"
    themes_yaml.write_text(original, encoding="utf-8")
    changelog = tmp_path / "changelog.md"

    report = apply_themes(themes_yaml, _result_one_propose(),
                          changelog_path=changelog, approver="tester", snapshot="s")

    assert report.applied == []                 # themes are human-added only
    assert len(report.deferred) == 1
    assert themes_yaml.read_text(encoding="utf-8") == original   # never edited
    assert changelog.exists() and "theme discovery review" in changelog.read_text(encoding="utf-8")
