"""Benchmark resolution incl. per-icp_client cohorts (src/cde/signals/benchmarks.py)."""
import pytest

from cde.signals.benchmarks import get_benchmark_value, benchmark_gap

CFG = {
    "benchmarks": {
        "crt": {
            "default": 1410,
            "by_icp_client": {"mob-at&t": 930, "pss-verizon": 1700},
            "by_call_type": {"claims": 1300},
        },
        "transfer_rate": 0.12,  # bare scalar
    }
}


def test_icp_client_takes_precedence():
    assert get_benchmark_value("crt", None, CFG, icp_client="mob-at&t") == 930
    assert get_benchmark_value("crt", None, CFG, icp_client="pss-verizon") == 1700


def test_icp_client_is_case_insensitive():
    # source data is 'MOB-AT&T'; config keys are lowercase
    assert get_benchmark_value("crt", None, CFG, icp_client="MOB-AT&T") == 930


def test_falls_back_to_call_type_then_default():
    assert get_benchmark_value("crt", "claims", CFG, icp_client="unknown-cohort") == 1300
    assert get_benchmark_value("crt", None, CFG, icp_client="unknown-cohort") == 1410
    assert get_benchmark_value("crt", None, CFG) == 1410


def test_bare_scalar_benchmark():
    assert get_benchmark_value("transfer_rate", None, CFG, icp_client="mob-at&t") == 0.12


def test_missing_metric_returns_none():
    assert get_benchmark_value("does_not_exist", None, CFG, icp_client="mob-at&t") is None


def test_benchmark_gap_signed():
    assert benchmark_gap(0.20, 0.12) == pytest.approx(0.08)
    assert benchmark_gap(0.05, 0.12) == pytest.approx(-0.07)
    assert benchmark_gap(1.0, None) is None
