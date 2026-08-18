from datetime import date, timedelta

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.classic_patterns import (
    PatternConfig,
    PatternState,
    PatternType,
    detect_double_patterns,
)
from stock_harness.trend_pivots import PivotKind, PricePivot


def _bars(closes: list[float], volumes: list[int] | None = None) -> list[AnalysisBar]:
    start = date(2026, 1, 1)
    volumes = volumes or [100] * len(closes)
    return [
        AnalysisBar(
            period_start=start + timedelta(days=index),
            period_end=start + timedelta(days=index),
            open=close, high=close + 0.3, low=close - 0.3, close=close,
            volume=volumes[index], sources=("test",), contains_provisional=False,
            period_complete=True, observed_at_ms=index,
        )
        for index, close in enumerate(closes)
    ]


def _pivot(kind: PivotKind, index: int, price: float) -> PricePivot:
    day = date(2026, 1, 1) + timedelta(days=index)
    return PricePivot(kind, day, price, day + timedelta(days=1), 0.5, False)


CONFIG = PatternConfig(minimum_leg_bars=2, minimum_prominence_percent=0.05)


def test_detects_confirmed_double_bottom_with_chinese_name_and_neckline():
    bars = _bars([10, 10.5, 11, 12, 11, 10.2, 11, 12.5], [100] * 7 + [200])
    pivots = [
        _pivot(PivotKind.LOW, 0, 9.7),
        _pivot(PivotKind.HIGH, 3, 12.3),
        _pivot(PivotKind.LOW, 5, 9.9),
    ]

    patterns = detect_double_patterns(bars, pivots, CONFIG)

    assert len(patterns) == 1
    assert patterns[0].pattern_type is PatternType.DOUBLE_BOTTOM
    assert patterns[0].display_name == "双底"
    assert patterns[0].state is PatternState.CONFIRMED
    assert patterns[0].neckline_price == 12.3
    assert patterns[0].breakout_date == bars[-1].period_end
    assert patterns[0].volume_ratio == 2
    assert patterns[0].primary is True


def test_detects_forming_double_top_without_backdating_confirmation():
    bars = _bars([12, 11, 10, 9, 10, 11.8, 11.2])
    pivots = [
        _pivot(PivotKind.HIGH, 0, 12.3),
        _pivot(PivotKind.LOW, 3, 8.7),
        _pivot(PivotKind.HIGH, 5, 12.1),
    ]

    pattern = detect_double_patterns(bars, pivots, CONFIG)[0]

    assert pattern.pattern_type is PatternType.DOUBLE_TOP
    assert pattern.display_name == "双顶"
    assert pattern.state is PatternState.FORMING
    assert pattern.available_date == pivots[-1].confirmed_date
    assert pattern.breakout_date is None


def test_confirms_double_top_only_after_a_post_availability_neckline_break():
    bars = _bars([12, 11, 10, 9, 10, 11.8, 10, 8.5], [100] * 7 + [190])
    pivots = [
        _pivot(PivotKind.HIGH, 0, 12.3),
        _pivot(PivotKind.LOW, 3, 8.7),
        _pivot(PivotKind.HIGH, 5, 12.1),
    ]

    pattern = detect_double_patterns(bars, pivots, CONFIG)[0]

    assert pattern.pattern_type is PatternType.DOUBLE_TOP
    assert pattern.state is PatternState.CONFIRMED
    assert pattern.breakout_date == bars[-1].period_end
    assert pattern.volume_ratio == 1.9


def test_marks_double_bottom_invalid_after_close_below_invalidation_level():
    bars = _bars([10, 10.5, 11, 12, 11, 10.2, 9.5])
    pivots = [
        _pivot(PivotKind.LOW, 0, 9.7),
        _pivot(PivotKind.HIGH, 3, 12.3),
        _pivot(PivotKind.LOW, 5, 9.9),
    ]

    pattern = detect_double_patterns(bars, pivots, CONFIG)[0]

    assert pattern.state is PatternState.INVALIDATED
    assert pattern.invalidation_date == bars[-1].period_end


def test_rejects_lookalike_with_unequal_endpoints_or_weak_prominence():
    bars = _bars([10, 11, 12, 12.2, 12, 11.5, 12])
    unequal = [
        _pivot(PivotKind.LOW, 0, 9.7),
        _pivot(PivotKind.HIGH, 3, 12.5),
        _pivot(PivotKind.LOW, 5, 11.2),
    ]
    weak = [
        _pivot(PivotKind.LOW, 0, 10),
        _pivot(PivotKind.HIGH, 3, 10.3),
        _pivot(PivotKind.LOW, 5, 10.1),
    ]

    assert detect_double_patterns(bars, unequal, CONFIG) == ()
    assert detect_double_patterns(bars, weak, CONFIG) == ()
