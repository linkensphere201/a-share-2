from datetime import date, timedelta

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.trend_pivots import (
    DirectionalChangeConfig,
    PivotKind,
    detect_directional_change_pivots,
)


def _bars(
    values: list[float], start: date = date(2026, 1, 1)
) -> list[AnalysisBar]:
    return [
        AnalysisBar(
            period_start=start + timedelta(days=index),
            period_end=start + timedelta(days=index),
            open=value, high=value + 0.2, low=value - 0.2, close=value,
            volume=100, sources=("test",), contains_provisional=False,
            period_complete=True, observed_at_ms=index,
        )
        for index, value in enumerate(values)
    ]


CONFIG = DirectionalChangeConfig(
    atr_period=3, atr_multiplier=0.5, minimum_reversal_percent=0.05
)


def test_directional_change_pivots_alternate_and_keep_confirmation_dates():
    bars = _bars([10, 9, 8, 9, 10, 11, 10, 9, 8, 9, 10])

    pivots = detect_directional_change_pivots(bars, CONFIG)

    assert [item.kind for item in pivots] == [
        PivotKind.HIGH, PivotKind.LOW, PivotKind.HIGH, PivotKind.LOW,
        PivotKind.HIGH,
    ]
    assert pivots[1].pivot_date == bars[2].period_end
    assert pivots[1].confirmed_date == bars[3].period_end
    assert pivots[2].pivot_date == bars[5].period_end
    assert pivots[2].confirmed_date == bars[6].period_end
    assert pivots[-1].tentative is True
    assert pivots[-1].confirmed_date is None


def test_future_suffix_cannot_change_pivots_visible_at_historical_cutoff():
    prefix = _bars([10, 9, 8, 9, 10, 11, 10])
    ordinary = detect_directional_change_pivots(prefix, CONFIG)
    mutated = detect_directional_change_pivots(
        [*prefix, *_bars([100, 1, 200], prefix[-1].period_end + timedelta(days=1))],
        CONFIG,
    )

    visible = tuple(
        item for item in mutated
        if item.confirmed_date is not None and item.confirmed_date <= prefix[-1].period_end
    )
    confirmed = tuple(item for item in ordinary if not item.tentative)
    assert visible == confirmed


def test_flat_series_has_only_one_tentative_pivot():
    pivots = detect_directional_change_pivots(_bars([10, 10, 10, 10]), CONFIG)

    assert len(pivots) == 1
    assert pivots[0].tentative is True
