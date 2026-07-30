"""Deterministic value computation, incl. average/avg calc-name reconciliation."""
from cde.signals.build_signals import _compute_value_row


def _compute(calc, num, den, handlers, default=None, prefer_value=False, raw=None):
    return _compute_value_row(
        numerator=num,
        denominator=den,
        calculation=calc,
        raw_value=raw,
        prefer_value=prefer_value,
        default_calculation=default,
        handlers=handlers,
    )


def test_average_calc_is_normalized_to_avg():
    # metric_catalog uses "average"; source handlers key it as "avg". Must still divide.
    v = _compute("average", 100.0, 10.0, handlers={"avg": {"denominator_min": 1}})
    assert v == 10.0


def test_rate_respects_denominator_min():
    # denominator below the handler floor -> not enough evidence -> None
    v = _compute("rate", 5.0, 2.0, handlers={"rate": {"denominator_min": 10}})
    assert v is None


def test_rate_computes_when_denominator_sufficient():
    v = _compute("rate", 5.0, 20.0, handlers={"rate": {"denominator_min": 10}})
    assert abs(v - 0.25) < 1e-9


def test_prefer_value_short_circuits():
    v = _compute("rate", 5.0, 20.0, handlers={"rate": {}}, prefer_value=True, raw=0.99)
    assert v == 0.99


def test_score_calc_returns_numerator():
    v = _compute("score", 7.0, None, handlers={"score": {}})
    assert v == 7.0
