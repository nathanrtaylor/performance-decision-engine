"""Config-driven guardrail thresholds for the two propose-only modules.

Additive-safety contract: an absent/empty tuning block must reproduce the
hand-curated default dataclass exactly, so today's proposals are unchanged.
A recognized override changes only that field; unknown keys are ignored.
"""
import dataclasses

from cde.benchmarks_recalc.config import RecalcThresholds
from cde.themes_discovery.config import DiscoveryThresholds


def test_recalc_empty_config_reproduces_defaults():
    assert RecalcThresholds.from_config(None) == RecalcThresholds()
    assert RecalcThresholds.from_config({}) == RecalcThresholds()
    assert RecalcThresholds.from_config({"benchmark_recalc": {}}) == RecalcThresholds()


def test_discovery_empty_config_reproduces_defaults():
    assert DiscoveryThresholds.from_config(None) == DiscoveryThresholds()
    assert DiscoveryThresholds.from_config({}) == DiscoveryThresholds()
    assert DiscoveryThresholds.from_config({"theme_discovery": {}}) == DiscoveryThresholds()


def test_recalc_recognized_override_applies():
    thr = RecalcThresholds.from_config({"benchmark_recalc": {"min_agents_cohort": 99}})
    assert thr.min_agents_cohort == 99
    # every other field is untouched
    default = RecalcThresholds()
    for f in dataclasses.fields(RecalcThresholds):
        if f.name != "min_agents_cohort":
            assert getattr(thr, f.name) == getattr(default, f.name)


def test_discovery_recognized_override_applies():
    thr = DiscoveryThresholds.from_config({"theme_discovery": {"min_correlation": 0.75}})
    assert thr.min_correlation == 0.75
    default = DiscoveryThresholds()
    for f in dataclasses.fields(DiscoveryThresholds):
        if f.name != "min_correlation":
            assert getattr(thr, f.name) == getattr(default, f.name)


def test_unknown_keys_are_ignored():
    # A stray/typo'd knob must not crash and must not alter recognized fields.
    thr = RecalcThresholds.from_config({"benchmark_recalc": {"not_a_real_knob": 1}})
    assert thr == RecalcThresholds()
