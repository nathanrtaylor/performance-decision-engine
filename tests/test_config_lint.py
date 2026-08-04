"""Config referential-integrity linter (src/cde/governance/config_lint.py).

Guards the additive-safety contract: the shipped config must pass with zero
errors, and each cross-reference break must surface as an error.
"""
from pathlib import Path

import pytest

from cde.governance.config_lint import lint_config
from cde.governance.versioning import resolve_active_config

CONFIGS = Path(__file__).resolve().parents[1] / "configs"


@pytest.fixture(scope="module")
def cfg():
    return resolve_active_config(CONFIGS)


def _metrics(c):
    return c["metric_catalog"]["metric_catalog"]["metrics"]


def _themes(c):
    t = c["themes"]
    return t["themes"] if "themes" in t else t


def test_shipped_config_has_no_errors(cfg):
    # Additive contract: today's valid config must pass (only future mistakes fail).
    assert lint_config(cfg).ok()


def test_blank_direction_is_error(cfg):
    import copy

    c = copy.deepcopy(cfg)
    victim = next(iter(_metrics(c)))
    _metrics(c)[victim]["direction"] = ""
    report = lint_config(c)
    assert not report.ok()
    assert any("invalid direction" in e for e in report.errors)


def test_unknown_direction_is_error(cfg):
    import copy

    c = copy.deepcopy(cfg)
    victim = next(iter(_metrics(c)))
    _metrics(c)[victim]["direction"] = "higher_is_bettr"  # typo
    assert not lint_config(c).ok()


def test_theme_member_not_a_metric_is_error(cfg):
    import copy

    c = copy.deepcopy(cfg)
    themes = _themes(c)
    tname = next(iter(themes))
    themes[tname]["members"] = list(themes[tname].get("members", [])) + ["not_a_real_metric"]
    report = lint_config(c)
    assert not report.ok()
    assert any("is not a metric" in e for e in report.errors)


def test_missing_config_benchmark_is_error(cfg):
    import copy

    c = copy.deepcopy(cfg)
    for name, d in _metrics(c).items():
        if (d.get("benchmark") or {}).get("type") == "config" and name in c["benchmarks"]:
            del c["benchmarks"][name]
            break
    report = lint_config(c)
    assert not report.ok()
    assert any("no entry in benchmarks.yaml" in e for e in report.errors)


def test_eligible_metric_without_topic_is_error(cfg):
    import copy

    c = copy.deepcopy(cfg)
    tm = c["topic_map"]["topic_map"]["metric_to_topic"]
    # find an eligible metric and remove its topic mapping
    for name, d in _metrics(c).items():
        if d.get("eligible_for_prioritization") and name in tm:
            del tm[name]
            report = lint_config(c)
            assert not report.ok()
            assert any(name in e and "no topic" in e for e in report.errors)
            return
    pytest.skip("no eligible metric with a topic mapping to remove")


def test_governance_require_unit_is_enforced_when_declared(cfg):
    import copy

    c = copy.deepcopy(cfg)
    mc = c["metric_catalog"]["metric_catalog"]
    mc.setdefault("governance", {})["require_unit"] = True
    victim = next(iter(_metrics(c)))
    _metrics(c)[victim].pop("unit", None)
    report = lint_config(c)
    assert not report.ok()
    assert any("'unit'" in e for e in report.errors)


def test_governance_flag_off_disables_check(cfg):
    import copy

    c = copy.deepcopy(cfg)
    mc = c["metric_catalog"]["metric_catalog"]
    mc.setdefault("governance", {})["require_unit"] = False
    victim = next(iter(_metrics(c)))
    _metrics(c)[victim].pop("unit", None)
    # unit missing but the flag is off -> not an error
    assert not any("'unit'" in e for e in lint_config(c).errors)


def test_config_hash_stable_and_content_sensitive(cfg):
    import copy

    from cde.governance.versioning import config_content_hash

    base = config_content_hash(cfg)
    # per-run snapshot stamp must not change the content hash
    c = copy.deepcopy(cfg)
    c.setdefault("meta", {})["data_snapshot"] = "run_xyz"
    assert config_content_hash(c) == base
    # changing an actual config value must change the hash
    c2 = copy.deepcopy(cfg)
    c2["abstention"]["min_priority_score"] = 0.999
    assert config_content_hash(c2) != base
