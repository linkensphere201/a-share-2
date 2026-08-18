from datetime import date, timedelta

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.consolidation_patterns import (
    ConsolidationConfig,
    ConsolidationType,
    detect_consolidation_patterns,
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


CONFIG = ConsolidationConfig(minimum_duration_bars=6)


def test_detects_rectangle_from_repeated_flat_boundaries():
    bars = _bars([10, 11, 12, 11, 10, 11, 12, 11, 10.1, 11, 12.1])
    pivots = [
        _pivot(PivotKind.LOW, 0, 10), _pivot(PivotKind.HIGH, 2, 12),
        _pivot(PivotKind.LOW, 4, 10), _pivot(PivotKind.HIGH, 6, 12),
        _pivot(PivotKind.LOW, 8, 10.1), _pivot(PivotKind.HIGH, 10, 12.1),
    ]

    patterns = detect_consolidation_patterns(bars, pivots, CONFIG)

    assert any(item.pattern_type is ConsolidationType.RECTANGLE for item in patterns)
    rectangle = next(item for item in patterns if item.pattern_type is ConsolidationType.RECTANGLE)
    assert rectangle.display_name == "震荡平台"
    assert rectangle.upper_boundary.end_price > rectangle.lower_boundary.end_price


def test_detects_symmetrical_triangle_and_rejects_crossed_boundaries():
    bars = _bars([9, 11, 13, 11, 9.5, 11, 12, 11, 10, 11, 11.2])
    pivots = [
        _pivot(PivotKind.LOW, 0, 9), _pivot(PivotKind.HIGH, 2, 13),
        _pivot(PivotKind.LOW, 4, 9.5), _pivot(PivotKind.HIGH, 6, 12),
        _pivot(PivotKind.LOW, 8, 10), _pivot(PivotKind.HIGH, 10, 11.2),
    ]

    patterns = detect_consolidation_patterns(bars, pivots, CONFIG)

    assert any(item.pattern_type is ConsolidationType.SYMMETRICAL_TRIANGLE for item in patterns)
    triangle = next(item for item in patterns if item.pattern_type is ConsolidationType.SYMMETRICAL_TRIANGLE)
    assert triangle.score_components["contraction"] > 0
    crossed = [
        _pivot(PivotKind.LOW, 0, 10), _pivot(PivotKind.HIGH, 2, 12),
        _pivot(PivotKind.LOW, 4, 11), _pivot(PivotKind.HIGH, 6, 10.5),
    ]
    assert detect_consolidation_patterns(bars[:7], crossed, CONFIG) == ()


def test_uses_only_post_availability_close_for_breakout_confirmation():
    bars = _bars([10, 11, 12, 11, 10, 11, 12, 12.5])
    pivots = [
        _pivot(PivotKind.LOW, 0, 10), _pivot(PivotKind.HIGH, 2, 12),
        _pivot(PivotKind.LOW, 4, 10), _pivot(PivotKind.HIGH, 6, 12),
    ]

    pattern = detect_consolidation_patterns(bars, pivots, CONFIG)[0]

    assert pattern.available_date == pivots[-1].confirmed_date
    assert pattern.breakout_date == bars[-1].period_end
    assert pattern.completion_state == "confirmed"


def test_does_not_force_parallel_directional_swings_into_a_named_pattern():
    bars = _bars([9, 10, 11, 10, 10, 11, 12, 11, 11, 12, 13])
    pivots = [
        _pivot(PivotKind.LOW, 0, 9), _pivot(PivotKind.HIGH, 2, 11),
        _pivot(PivotKind.LOW, 4, 10), _pivot(PivotKind.HIGH, 6, 12),
        _pivot(PivotKind.LOW, 8, 11), _pivot(PivotKind.HIGH, 10, 13),
    ]

    assert detect_consolidation_patterns(bars, pivots, CONFIG) == ()


def test_flag_requires_prior_impulse_before_parallel_countertrend_channel():
    bars = _bars([
        8, 8.2, 8.5, 9, 10, 11, 12,
        11.8, 11.5, 11.7, 11.3, 11.5, 11.1, 11.3,
    ])
    pivots = [
        _pivot(PivotKind.HIGH, 6, 12.2), _pivot(PivotKind.LOW, 8, 11.3),
        _pivot(PivotKind.HIGH, 9, 11.9), _pivot(PivotKind.LOW, 10, 11.1),
        _pivot(PivotKind.HIGH, 11, 11.7), _pivot(PivotKind.LOW, 12, 10.9),
    ]
    config = ConsolidationConfig(minimum_duration_bars=5)

    patterns = detect_consolidation_patterns(bars, pivots, config)

    assert any(item.pattern_type is ConsolidationType.BULL_FLAG for item in patterns)
    flag = next(item for item in patterns if item.pattern_type is ConsolidationType.BULL_FLAG)
    assert flag.display_name == "多头旗形"
    assert flag.score_components["prior_impulse"] > 0
