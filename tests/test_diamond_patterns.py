from datetime import date, timedelta

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.diamond_patterns import (
    DiamondConfig,
    DiamondType,
    detect_diamond_patterns,
)
from stock_harness.trend_pivots import PivotKind, PricePivot


def _bars(closes: list[float]) -> list[AnalysisBar]:
    start = date(2026, 1, 1)
    return [
        AnalysisBar(
            start + timedelta(days=index), start + timedelta(days=index),
            close, close + 0.2, close - 0.2, close, 100,
            ("test",), False, True, index,
        )
        for index, close in enumerate(closes)
    ]


def _pivot(kind: PivotKind, index: int, price: float) -> PricePivot:
    day = date(2026, 1, 1) + timedelta(days=index)
    return PricePivot(kind, day, price, day + timedelta(days=1), 0.5, False)


CONFIG = DiamondConfig(minimum_duration_bars=7)


def test_detects_broadening_then_contracting_diamond():
    bars = _bars([10, 12, 9, 13, 9.5, 12.5, 10, 12, 11])
    pivots = [
        _pivot(PivotKind.LOW, 0, 10), _pivot(PivotKind.HIGH, 1, 12),
        _pivot(PivotKind.LOW, 2, 9), _pivot(PivotKind.HIGH, 3, 13),
        _pivot(PivotKind.LOW, 4, 9.5), _pivot(PivotKind.HIGH, 5, 12.5),
        _pivot(PivotKind.LOW, 6, 10), _pivot(PivotKind.HIGH, 7, 12),
    ]

    patterns = detect_diamond_patterns(bars, pivots, CONFIG)

    assert patterns
    assert patterns[0].pattern_type is DiamondType.DIAMOND
    assert patterns[0].display_name == "菱形"
    assert patterns[0].score_components["expansion"] > 0
    assert patterns[0].score_components["contraction"] > 0
    assert len(patterns[0].boundary_segments) == 4


def test_ordinary_symmetrical_triangle_is_not_a_diamond():
    bars = _bars([10, 13, 10.2, 12.5, 10.5, 12, 10.8, 11.5])
    pivots = [
        _pivot(PivotKind.LOW, 0, 10), _pivot(PivotKind.HIGH, 1, 13),
        _pivot(PivotKind.LOW, 2, 10.2), _pivot(PivotKind.HIGH, 3, 12.5),
        _pivot(PivotKind.LOW, 4, 10.5), _pivot(PivotKind.HIGH, 5, 12),
        _pivot(PivotKind.LOW, 6, 10.8), _pivot(PivotKind.HIGH, 7, 11.5),
    ]

    assert detect_diamond_patterns(bars, pivots, CONFIG) == ()


def test_prior_uptrend_classifies_diamond_top_context():
    bars = _bars([8, 8.5, 9, 10, 12, 9, 13, 9.5, 12.5, 10, 12, 11])
    pivots = [
        _pivot(PivotKind.LOW, 3, 10), _pivot(PivotKind.HIGH, 4, 12),
        _pivot(PivotKind.LOW, 5, 9), _pivot(PivotKind.HIGH, 6, 13),
        _pivot(PivotKind.LOW, 7, 9.5), _pivot(PivotKind.HIGH, 8, 12.5),
        _pivot(PivotKind.LOW, 9, 10), _pivot(PivotKind.HIGH, 10, 12),
    ]

    pattern = detect_diamond_patterns(bars, pivots, CONFIG)[0]

    assert pattern.pattern_type is DiamondType.DIAMOND_TOP
    assert pattern.context_change_percent > 0
