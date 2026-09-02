"""Per-metric display formatting (src/pde/utils/metric_format.py).

Presentation only: verifies scale/suffix/decimals resolution (per-metric over category default)
and value formatting, including the scale-preserving invariant that value/benchmark/gap share a
transform.
"""
import math

from pde.utils.metric_format import display_map, format_value, resolve_display


def _catalog():
    return {
        "metric_catalog": {
            "metrics": {
                "nsp100": {"category": "business", "display": {"scale": 100, "suffix": "", "decimals": 1}},
                "transfer_rate": {"category": "business", "display": {"scale": 100, "suffix": "%", "decimals": 1}},
                "erp": {"category": "business", "display": {"scale": 1, "suffix": "", "decimals": 0}},
                "crt": {"category": "business", "display": {"scale": 1, "suffix": "s", "decimals": 0}},
                "actively_listen": {"category": "quality_behavior"},   # inherits category default
                "no_spec": {"category": "business"},                   # no display anywhere
            },
            "category_defaults": {
                "business": {"base_weight_class": "high"},
                "quality_behavior": {"display": {"scale": 100, "suffix": "%", "decimals": 0}},
            },
        }
    }


def _cfg():
    return {"metric_catalog": _catalog()}


def test_resolve_display_per_metric_and_category_default():
    cat = _catalog()["metric_catalog"]
    assert resolve_display(cat, "nsp100") == {"scale": 100, "suffix": "", "decimals": 1}
    # behavior inherits the category default (no per-metric block)
    assert resolve_display(cat, "actively_listen") == {"scale": 100, "suffix": "%", "decimals": 0}
    # metric with no display and a category lacking display -> {}
    assert resolve_display(cat, "no_spec") == {}


def test_per_metric_display_overrides_category_default():
    cat = _catalog()["metric_catalog"]
    cat["metrics"]["actively_listen"]["display"] = {"scale": 1, "suffix": "", "decimals": 2}
    assert resolve_display(cat, "actively_listen") == {"scale": 1, "suffix": "", "decimals": 2}


def test_display_map_only_includes_metrics_with_spec():
    dm = display_map(_cfg())
    assert dm.get("nsp100") == {"scale": 100, "suffix": "", "decimals": 1}
    assert "actively_listen" in dm            # via category default
    assert "no_spec" not in dm                # no spec at all


def test_format_value_examples():
    dm = display_map(_cfg())
    assert format_value(0.027, dm["nsp100"]) == "2.7"
    assert format_value(0.10, dm["transfer_rate"]) == "10.0%"
    assert format_value(90.0, dm["erp"]) == "90"
    assert format_value(1380.0, dm["crt"]) == "1,380s"
    assert format_value(0.95, dm["actively_listen"]) == "95%"


def test_format_value_uniform_across_value_benchmark_gap():
    # A metric's transform is applied identically to value/benchmark/gap (they share a scale).
    spec = display_map(_cfg())["transfer_rate"]
    assert format_value(0.123, spec) == "12.3%"
    assert format_value(0.10, spec) == "10.0%"
    assert format_value(0.023, spec) == "2.3%"      # gap
    assert format_value(-0.02, spec) == "-2.0%"     # negative gap keeps sign


def test_format_value_missing_and_no_spec():
    spec = display_map(_cfg())["nsp100"]
    assert format_value(None, spec) == "—"
    assert format_value(float("nan"), spec) == "—"
    assert format_value("junk", spec) == "—"
    # no spec -> None, so callers keep their own default formatting
    assert format_value(0.5, None) is None
    assert format_value(0.5, {}) is None
