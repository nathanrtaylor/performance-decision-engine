"""Tests for the guardrail-gated benchmark recalculation module (src/cde/benchmarks_recalc)."""
from __future__ import annotations

import pandas as pd
import pytest

from cde.benchmarks_recalc import config as C
from cde.benchmarks_recalc.apply import apply_benchmarks
from cde.benchmarks_recalc.compare import BenchmarkDiffRow, CompareResult, compare
from cde.benchmarks_recalc.config import RecalcThresholds
from cde.benchmarks_recalc.dashboard import build_recalc_dashboard_html
from cde.benchmarks_recalc.guardrails import evaluate
from cde.benchmarks_recalc.prep import RawFrames, prep_frames, windowed_mean_per_agent
from cde.benchmarks_recalc.recompute import CohortStat, recompute_all

WEEKS = [  # 8 distinct week-ending dates
    "2026-06-05", "2026-06-12", "2026-06-19", "2026-06-26",
    "2026-07-03", "2026-07-10", "2026-07-17", "2026-07-24",
]


# ---------------------------------------------------------------------------------------------------
# synthetic-frame builders
# ---------------------------------------------------------------------------------------------------

def _am_rows(metric_raw, cohort, agents_calc, denom=100.0):
    """agent_metrics rows: each agent gets the same calc across all 8 weeks (windowed mean == calc)."""
    rows = []
    for agent_id, calc in agents_calc:
        for wk in WEEKS:
            rows.append(dict(agent_id=agent_id, week_ending=wk, call_type="all", metric=metric_raw,
                             icp_client=cohort, site="turtles", numerator=calc * denom,
                             denominator=denom, calc=calc))
    return rows


def _bs_rows(behavior_raw, scorecard, cohort, agents_calc, denom=100.0):
    rows = []
    for agent_id, calc in agents_calc:
        for wk in WEEKS:
            rows.append(dict(agent_id=agent_id, week_ending=wk, call_type="all",
                             scorecard_name=scorecard, behavior=behavior_raw,
                             numerator=calc * denom, denominator=denom, calc=calc))
    return rows


def _agents_rows(cohort, agent_ids):
    rows = []
    for agent_id in agent_ids:
        for wk in WEEKS:
            rows.append(dict(week_ending=wk, agent_id=agent_id, mascot="turtles", icp_client=cohort,
                             tenure_group="180+", coach="x", coach_id=1))
    return rows


def _config(metrics: dict, benchmarks: dict) -> dict:
    return {"metric_catalog": {"metric_catalog": {"metrics": metrics}}, "benchmarks": benchmarks}


def _op_meta(name, raw, denom_min=None):
    entry = {"source": "agent_metrics", "source_metric_key": raw, "category": "business",
             "direction": "lower_is_better", "unit": "rate", "benchmark": {"type": "config", "key": name}}
    if denom_min is not None:
        entry["computation_override"] = {"denominator_min": denom_min}
    return entry


def _beh_meta(name, raw):
    return {"source": "behavior_scores", "source_metric_key": raw, "category": "quality_behavior",
            "direction": "higher_is_better", "unit": "score", "benchmark": {"type": "config", "key": name}}


def _raw(agent_metrics=None, behavior_scores=None, agents=None):
    return RawFrames(
        agents=pd.DataFrame(agents) if agents else pd.DataFrame(),
        agent_metrics=pd.DataFrame(agent_metrics) if agent_metrics else pd.DataFrame(),
        behavior_scores=pd.DataFrame(behavior_scores) if behavior_scores else pd.DataFrame(),
        raw_dir=None, snapshot_id="test-snap",
    )


THR = RecalcThresholds()


# ---------------------------------------------------------------------------------------------------
# prep / windowed mean
# ---------------------------------------------------------------------------------------------------

def test_windowed_mean_is_mean_over_last_8_weeks():
    # agent with varying weekly calc; mean should be the average across the 8 kept weeks
    rows = []
    for i, wk in enumerate(WEEKS):
        rows.append(dict(agent_id="a1", week_ending=wk, call_type="all", metric="client transfers",
                         icp_client="mob-at&t", site="t", numerator=0, denominator=100, calc=i / 10.0))
    cfg = _config({"transfer_rate": _op_meta("transfer_rate", "client transfers")}, {})
    prepped = prep_frames(_raw(agent_metrics=rows), cfg)
    wm = windowed_mean_per_agent(prepped.agent_metrics, "transfer_rate")
    assert wm.loc[0, "mean_calc"] == pytest.approx(sum(range(8)) / 10.0 / 8)
    assert wm.loc[0, "n_weeks"] == 8


def test_denominator_min_drops_thin_weeks():
    rows = _am_rows("client transfers", "mob-at&t", [("a1", 0.5)], denom=5.0)  # denom 5 < min 20
    cfg = _config({"transfer_rate": _op_meta("transfer_rate", "client transfers", denom_min=20)}, {})
    prepped = prep_frames(_raw(agent_metrics=rows), cfg)
    wm = windowed_mean_per_agent(prepped.agent_metrics, "transfer_rate", denominator_min=20)
    assert wm.empty  # all weekly rows dropped as thin


def test_icp_client_case_normalized():
    rows = _am_rows("client transfers", "MOB-AT&T", [("a1", 0.1)])  # mixed case in source
    cfg = _config({"transfer_rate": _op_meta("transfer_rate", "client transfers")}, {})
    prepped = prep_frames(_raw(agent_metrics=rows), cfg)
    assert set(prepped.agent_metrics["icp_client"].unique()) == {"mob-at&t"}


# ---------------------------------------------------------------------------------------------------
# recompute per category
# ---------------------------------------------------------------------------------------------------

def test_operational_median_and_cohort_sufficiency():
    big = _am_rows("client transfers", "pss-verizon", [(f"p{i}", 0.10) for i in range(20)])
    small = _am_rows("client transfers", "mob-verizon", [(f"m{i}", 0.05) for i in range(5)])
    cfg = _config({"transfer_rate": _op_meta("transfer_rate", "client transfers")},
                  {"transfer_rate": {"default": 0.10}})
    prepped = prep_frames(_raw(agent_metrics=big + small), cfg)
    cand = recompute_all(prepped, THR)["transfer_rate"]
    assert cand.category == C.CAT_OPERATIONAL
    assert cand.by_icp_client["pss-verizon"].value == pytest.approx(0.10)
    assert cand.by_icp_client["pss-verizon"].sufficient is True     # 20 >= 15
    assert cand.by_icp_client["mob-verizon"].sufficient is False    # 5 < 15


def test_absolute_default_degeneracy_kept():
    rows = _am_rows("cancellation rate", "pss-verizon", [(f"p{i}", 0.0) for i in range(30)])  # floor
    meta = {"cancel_rate": {"source": "agent_metrics", "source_metric_key": "cancellation rate",
                            "category": "business", "direction": "lower_is_better", "unit": "rate",
                            "benchmark": {"type": "config", "key": "cancel_rate"}}}
    cfg = _config(meta, {"cancel_rate": 0.12})
    prepped = prep_frames(_raw(agent_metrics=rows), cfg)
    cand = recompute_all(prepped, THR)["cancel_rate"]
    assert cand.default.degenerate is True
    assert cand.default.value is None  # keep curated absolute target


def test_quality_p25_cap():
    # p25 above cap -> capped to 0.95
    rows = _bs_rows("Actively Listen", "HEROES Auto QA Reporting - VZW", "pss-verizon",
                    [(f"p{i}", 0.99) for i in range(30)])
    cfg = _config({"actively_listen": _beh_meta("actively_listen", "Actively Listen")},
                  {"actively_listen": {"default": 0.95}})
    prepped = prep_frames(_raw(behavior_scores=rows, agents=_agents_rows("pss-verizon", [f"p{i}" for i in range(30)])), cfg)
    cand = recompute_all(prepped, THR)["actively_listen"]
    assert cand.category == C.CAT_QUALITY
    assert cand.skipped is False                       # guard: value_lo must not land in `skipped`
    assert cand.value_lo is not None and cand.value_hi is not None
    assert cand.default.capped is True
    assert cand.default.value == pytest.approx(0.95)


def test_sentiment_split_rule_and_vzw_only():
    q = "Did the customer show signs of frustration and was that frustration ignored?"
    mob = _bs_rows(q, C.SENTIMENT_SCORECARD, "mob-verizon", [(f"m{i}", 0.90) for i in range(20)])
    pss = _bs_rows(q, C.SENTIMENT_SCORECARD, "pss-verizon", [(f"p{i}", 0.70) for i in range(20)])
    agents = _agents_rows("mob-verizon", [f"m{i}" for i in range(20)]) + \
             _agents_rows("pss-verizon", [f"p{i}" for i in range(20)])
    cfg = _config({"customer_frustration_sentiment": _beh_meta("customer_frustration_sentiment", q)},
                  {"customer_frustration_sentiment": {"default": 0.8}})
    prepped = prep_frames(_raw(behavior_scores=mob + pss, agents=agents), cfg)
    cand = recompute_all(prepped, THR)["customer_frustration_sentiment"]
    assert cand.category == C.CAT_SENTIMENT
    assert cand.split_applied is True
    assert set(cand.by_icp_client) == {"mob-verizon", "pss-verizon"}
    assert cand.by_icp_client["mob-verizon"].value == pytest.approx(0.90)


def test_sentiment_no_split_when_close():
    q = "Did the customer show signs of frustration and was that frustration ignored?"
    mob = _bs_rows(q, C.SENTIMENT_SCORECARD, "mob-verizon", [(f"m{i}", 0.80) for i in range(20)])
    pss = _bs_rows(q, C.SENTIMENT_SCORECARD, "pss-verizon", [(f"p{i}", 0.81) for i in range(20)])
    agents = _agents_rows("mob-verizon", [f"m{i}" for i in range(20)]) + \
             _agents_rows("pss-verizon", [f"p{i}" for i in range(20)])
    cfg = _config({"customer_frustration_sentiment": _beh_meta("customer_frustration_sentiment", q)},
                  {"customer_frustration_sentiment": {"default": 0.8}})
    prepped = prep_frames(_raw(behavior_scores=mob + pss, agents=agents), cfg)
    cand = recompute_all(prepped, THR)["customer_frustration_sentiment"]
    assert cand.split_applied is False
    assert cand.by_icp_client == {}


def test_tool_metric_skipped():
    meta = {"guided_flow_adoption": {"source": "smart_offer", "source_metric_key": "gfa",
                                     "category": "tool_usage", "direction": "higher_is_better",
                                     "unit": "rate", "benchmark": {"type": "config", "key": "guided_flow_adoption"}}}
    cfg = _config(meta, {"guided_flow_adoption": {"default": 0.65}})
    prepped = prep_frames(_raw(), cfg)
    cand = recompute_all(prepped, THR)["guided_flow_adoption"]
    assert cand.skipped is True


# ---------------------------------------------------------------------------------------------------
# guardrails
# ---------------------------------------------------------------------------------------------------

def _stat(value, n=30, sufficient=True, degenerate=False):
    return CohortStat("default", value, n, "median", value, sufficient, degenerate, False, "note")


def test_guardrail_materiality_propose_vs_unchanged():
    r_material = evaluate(_stat(0.20), current_value=0.10, category=C.CAT_OPERATIONAL,
                          value_lo=0.0, value_hi=1.0, thr=THR)
    assert r_material.verdict == C.PROPOSE
    r_small = evaluate(_stat(0.101), current_value=0.10, category=C.CAT_OPERATIONAL,
                       value_lo=0.0, value_hi=1.0, thr=THR)
    assert r_small.verdict == C.UNCHANGED


def test_guardrail_holds_on_insufficient_sample():
    r = evaluate(_stat(0.20, n=5, sufficient=False), current_value=0.10, category=C.CAT_OPERATIONAL,
                 value_lo=0.0, value_hi=1.0, thr=THR)
    assert r.verdict == C.HOLD


def test_guardrail_holds_on_outlier():
    r = evaluate(_stat(5.0), current_value=0.10, category=C.CAT_OPERATIONAL,
                 value_lo=0.0, value_hi=1.0, thr=THR)
    assert r.verdict == C.HOLD
    assert r.checks["within_observed_range"] is False


def test_guardrail_holds_on_degenerate():
    r = evaluate(_stat(None, degenerate=True), current_value=0.12, category=C.CAT_ABSOLUTE,
                 value_lo=0.0, value_hi=1.0, thr=THR)
    assert r.verdict == C.HOLD


# ---------------------------------------------------------------------------------------------------
# compare + dashboard + apply
# ---------------------------------------------------------------------------------------------------

def _diff(metric, cohort, old, new, verdict, cat=C.CAT_OPERATIONAL, is_new=False):
    delta = (new - old) if (old is not None and new is not None) else None
    return BenchmarkDiffRow(metric, cat, cohort, old, new, delta,
                            (delta / old) if (delta and old) else None, verdict, "j", is_new_cohort=is_new)


def test_dashboard_renders_sections_and_chips():
    result = CompareResult(
        rows=[_diff("transfer_rate", "default", 0.10, 0.20, C.PROPOSE)],
        counts={C.PROPOSE: 1, C.HOLD: 0, C.UNCHANGED: 0, C.SKIPPED: 0},
    )
    html = build_recalc_dashboard_html(result, {"snapshot": "s", "window_weeks": 8})
    assert html.startswith("<!doctype html")
    assert "Operational metrics" in html
    assert 'class="chip' in html
    assert "PROPOSE" in html
    assert "transfer_rate" in html


def test_apply_preserves_comments_and_appends_changelog(tmp_path):
    bench = tmp_path / "benchmarks.yaml"
    bench.write_text(
        "benchmarks:\n"
        "  # operational\n"
        "  transfer_rate:\n"
        "    default: 0.105       # median comment\n"
        "    by_icp_client:\n"
        "      mob-at&t: 0.10\n"
        "      pss-verizon: 0.133\n",
        encoding="utf-8",
    )
    changelog = tmp_path / "changelog.md"
    result = CompareResult(
        rows=[
            _diff("transfer_rate", "default", 0.105, 0.150, C.PROPOSE),
            _diff("transfer_rate", "pss-verizon", 0.133, 0.200, C.PROPOSE),
        ],
        counts={C.PROPOSE: 2},
    )
    report = apply_benchmarks(bench, result, changelog_path=changelog, approver="tester", snapshot="s")
    text = bench.read_text(encoding="utf-8")
    assert "# median comment" in text          # inline comment preserved
    assert "# operational" in text             # block comment preserved
    assert "default: 0.15" in text             # value updated
    assert "pss-verizon: 0.2" in text          # cohort value updated
    assert "mob-at&t: 0.10" in text            # untouched cohort intact
    assert len(report.applied) == 2
    assert changelog.exists() and "benchmark recalculation apply" in changelog.read_text(encoding="utf-8")


def test_apply_defers_new_cohort_split(tmp_path):
    bench = tmp_path / "benchmarks.yaml"
    bench.write_text("benchmarks:\n  transfer_rate:\n    default: 0.105\n", encoding="utf-8")
    result = CompareResult(
        rows=[_diff("transfer_rate", "mob-verizon", 0.105, 0.30, C.PROPOSE, is_new=True)],
        counts={C.PROPOSE: 1},
    )
    report = apply_benchmarks(bench, result, changelog_path=tmp_path / "cl.md", approver="t", snapshot="s")
    assert report.applied == []
    assert any("new cohort split" in s for s in report.skipped_structural)
    assert bench.read_text(encoding="utf-8") == "benchmarks:\n  transfer_rate:\n    default: 0.105\n"
