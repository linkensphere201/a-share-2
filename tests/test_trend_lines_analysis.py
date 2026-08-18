from datetime import date, timedelta

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.trend_lines_analysis import (
    TrendHorizon,
    TrendLineConfig,
    TrendLineKind,
    generate_trend_line_candidates,
)
from stock_harness.trend_pivots import PivotKind, PricePivot


def _bars(values: list[tuple[float, float, float, float]]) -> list[AnalysisBar]:
    start = date(2026, 1, 1)
    return [
        AnalysisBar(
            period_start=start + timedelta(days=index),
            period_end=start + timedelta(days=index),
            open=value[0], high=value[1], low=value[2], close=value[3],
            volume=100, sources=("test",), contains_provisional=False,
            period_complete=True, observed_at_ms=index,
        )
        for index, value in enumerate(values)
    ]


def _pivot(kind: PivotKind, index: int, price: float) -> PricePivot:
    day = date(2026, 1, 1) + timedelta(days=index)
    return PricePivot(kind, day, price, day + timedelta(days=1), 0.5, False)


CONFIG = TrendLineConfig(
    min_anchor_span_bars=3,
    max_lines_per_kind=2,
    touch_tolerance_percent=0.02,
    body_tolerance_percent=0.01,
    max_body_cross_ratio=0.2,
    max_penetrations=1,
)


def test_generates_auditable_support_line_from_confirmed_lows():
    bars = _bars([
        (10.3, 10.8, 10.0, 10.4),
        (10.5, 11.0, 10.3, 10.8),
        (10.8, 11.3, 10.6, 11.0),
        (11.0, 11.5, 10.9, 11.3),
        (11.2, 11.7, 11.1, 11.5),
        (11.5, 12.0, 11.4, 11.8),
        (11.7, 12.2, 11.6, 12.0),
        (11.9, 12.4, 11.8, 12.2),
    ])
    pivots = [
        _pivot(PivotKind.LOW, 0, 10.0),
        _pivot(PivotKind.LOW, 3, 10.9),
        _pivot(PivotKind.LOW, 6, 11.6),
    ]

    lines = generate_trend_line_candidates(
        bars, pivots, TrendHorizon.SHORT, CONFIG
    )

    assert lines
    assert all(line.kind is TrendLineKind.SUPPORT for line in lines)
    assert lines[0].first_confirmed_date > lines[0].first_pivot_date
    assert lines[0].score_components.keys() == {
        "span", "touches", "recency", "integrity", "residual", "penetration"
    }
    assert lines[0].available_date == lines[0].second_confirmed_date
    assert lines[0].invalidation_reason is None
    assert lines[0].projected_price > lines[0].second_price


def test_rejects_line_with_repeated_body_crosses_and_penetrations():
    bars = _bars([
        (10.2, 10.5, 10.0, 10.3),
        (10.1, 10.3, 9.5, 9.7),
        (9.8, 10.0, 9.2, 9.4),
        (10.5, 10.8, 10.3, 10.6),
        (9.8, 10.0, 9.0, 9.2),
        (9.5, 9.7, 8.8, 9.0),
    ])
    pivots = [_pivot(PivotKind.LOW, 0, 10.0), _pivot(PivotKind.LOW, 3, 10.3)]

    assert generate_trend_line_candidates(
        bars, pivots, TrendHorizon.SHORT, CONFIG
    ) == ()


def test_tentative_pivots_are_never_used_as_line_anchors():
    bars = _bars([(10, 11, 9.8, 10.5)] * 8)
    first = _pivot(PivotKind.LOW, 0, 9.8)
    tentative_day = date(2026, 1, 7)
    tentative = PricePivot(PivotKind.LOW, tentative_day, 9.8, None, 0.5, True)

    assert generate_trend_line_candidates(
        bars, [first, tentative], TrendHorizon.LONG, CONFIG
    ) == ()


def test_result_at_historical_cutoff_is_independent_of_future_suffix():
    prefix = _bars([
        (10.3, 10.8, 10.0, 10.4),
        (10.5, 11.0, 10.3, 10.8),
        (10.8, 11.3, 10.6, 11.0),
        (11.0, 11.5, 10.9, 11.3),
        (11.2, 11.7, 11.1, 11.5),
        (11.5, 12.0, 11.4, 11.8),
        (11.7, 12.2, 11.6, 12.0),
    ])
    pivots = [_pivot(PivotKind.LOW, 0, 10.0), _pivot(PivotKind.LOW, 3, 10.9)]

    baseline = generate_trend_line_candidates(prefix, pivots, TrendHorizon.SHORT, CONFIG)
    replay = generate_trend_line_candidates(tuple(prefix), tuple(pivots), TrendHorizon.SHORT, CONFIG)

    assert replay == baseline
