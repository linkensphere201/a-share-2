from datetime import date, timedelta

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.reversal_patterns import (
    ReversalConfig,
    ReversalType,
    detect_reversal_patterns,
)
from stock_harness.trend_pivots import PivotKind, PricePivot


def _bars(closes: list[float], volumes: list[int] | None = None) -> list[AnalysisBar]:
    start = date(2026, 1, 1)
    volumes = volumes or [100] * len(closes)
    return [
        AnalysisBar(
            start + timedelta(days=index), start + timedelta(days=index),
            close, close + 0.2, close - 0.2, close, volumes[index],
            ("test",), False, True, index,
        )
        for index, close in enumerate(closes)
    ]


def _pivot(kind: PivotKind, index: int, price: float) -> PricePivot:
    day = date(2026, 1, 1) + timedelta(days=index)
    return PricePivot(kind, day, price, day + timedelta(days=1), 0.5, False)


CONFIG = ReversalConfig(minimum_leg_bars=1, minimum_head_prominence_percent=0.05)


def test_detects_confirmed_v_bottom_only_after_recovery_threshold():
    bars = _bars([12, 11, 10, 9, 8, 9, 10, 11.2])
    pivot = _pivot(PivotKind.LOW, 4, 7.8)

    pattern = next(
        item for item in detect_reversal_patterns(bars, [pivot], CONFIG)
        if item.pattern_type is ReversalType.V_BOTTOM
    )

    assert pattern.display_name == "\u0056\u5f62\u5e95"
    assert pattern.available_date == bars[-1].period_end
    assert pattern.breakout_date == bars[-1].period_end
    assert len(pattern.boundary_segments) == 2


def test_detects_confirmed_inverted_v_top_after_a_causal_decline():
    bars = _bars([8, 9, 10, 11, 12, 11, 10, 8.8])
    pivot = _pivot(PivotKind.HIGH, 4, 12.2)

    pattern = next(
        item for item in detect_reversal_patterns(bars, [pivot], CONFIG)
        if item.pattern_type is ReversalType.V_TOP
    )

    assert pattern.display_name == "倒V形顶"
    assert pattern.available_date == bars[-1].period_end
    assert pattern.direction == "bearish"


def test_rejects_one_sided_spike_without_recovery():
    bars = _bars([12, 11, 10, 9, 8, 8.2, 8.4, 8.5])
    pivot = _pivot(PivotKind.LOW, 4, 7.8)

    assert detect_reversal_patterns(bars, [pivot], CONFIG) == ()


def test_detects_head_shoulders_top_and_sloped_neckline():
    bars = _bars([10, 12, 10, 14, 10.5, 12.2, 10, 9.8])
    pivots = [
        _pivot(PivotKind.HIGH, 1, 12.2), _pivot(PivotKind.LOW, 2, 9.8),
        _pivot(PivotKind.HIGH, 3, 14.2), _pivot(PivotKind.LOW, 4, 10.3),
        _pivot(PivotKind.HIGH, 5, 12.4),
    ]

    pattern = next(
        item for item in detect_reversal_patterns(bars, pivots, CONFIG)
        if item.pattern_type is ReversalType.HEAD_SHOULDERS_TOP
    )

    assert pattern.display_name == "\u5934\u80a9\u9876"
    assert pattern.state == "confirmed"
    assert pattern.neckline_slope_per_bar > 0
    assert pattern.breakout_date == bars[6].period_end
    assert pattern.available_date == pivots[-1].confirmed_date


def test_detects_head_shoulders_bottom_and_upward_neckline_break():
    bars = _bars([14, 12, 14, 10, 13.5, 11.8, 14, 14.2])
    pivots = [
        _pivot(PivotKind.LOW, 1, 11.8), _pivot(PivotKind.HIGH, 2, 14.2),
        _pivot(PivotKind.LOW, 3, 9.8), _pivot(PivotKind.HIGH, 4, 13.7),
        _pivot(PivotKind.LOW, 5, 11.6),
    ]

    pattern = next(
        item for item in detect_reversal_patterns(bars, pivots, CONFIG)
        if item.pattern_type is ReversalType.HEAD_SHOULDERS_BOTTOM
    )

    assert pattern.display_name == "头肩底"
    assert pattern.state == "confirmed"
    assert pattern.direction == "bullish"
    assert pattern.breakout_date == bars[6].period_end


def test_rejects_head_shoulders_with_unequal_shoulders_or_flat_head():
    bars = _bars([10, 12, 10, 13, 10, 14, 11])
    unequal = [
        _pivot(PivotKind.HIGH, 1, 12), _pivot(PivotKind.LOW, 2, 10),
        _pivot(PivotKind.HIGH, 3, 14), _pivot(PivotKind.LOW, 4, 10),
        _pivot(PivotKind.HIGH, 5, 13.5),
    ]
    flat_head = [
        _pivot(PivotKind.HIGH, 1, 12), _pivot(PivotKind.LOW, 2, 10),
        _pivot(PivotKind.HIGH, 3, 12.3), _pivot(PivotKind.LOW, 4, 10),
        _pivot(PivotKind.HIGH, 5, 12.1),
    ]

    patterns = detect_reversal_patterns(bars, unequal, CONFIG)
    assert not any(item.pattern_type is ReversalType.HEAD_SHOULDERS_TOP for item in patterns)
    patterns = detect_reversal_patterns(bars, flat_head, CONFIG)
    assert not any(item.pattern_type is ReversalType.HEAD_SHOULDERS_TOP for item in patterns)
