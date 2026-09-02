from dataclasses import replace
from datetime import date, timedelta

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.trend_line_envelope import (
    EnvelopeSide,
    dominates_prior_extremes,
    evaluate_trend_line_envelope,
)
from stock_harness.trend_pivots import causal_average_true_range


def _bars(lows: list[float]) -> tuple[AnalysisBar, ...]:
    start = date(2026, 1, 1)
    return tuple(
        AnalysisBar(
            start + timedelta(days=index), start + timedelta(days=index),
            low + 0.4, low + 0.8, low, low + 0.5, 100,
            ("test",), False, True, index,
        )
        for index, low in enumerate(lows)
    )


def test_lower_envelope_is_symmetric_and_checks_full_interval():
    bars = list(_bars([10.0, 10.4, 10.8, 9.0, 11.2, 11.5, 11.8]))
    bars[3] = replace(bars[3], open=11.1, high=11.4, close=11.0)
    integrity = evaluate_trend_line_envelope(
        bars, causal_average_true_range(bars), 0, 6, 10.0, 0.3, 6,
        [0, 2, 6], 1, EnvelopeSide.LOWER,
    )

    assert integrity.independent_touch_count == 1
    assert integrity.wick_breach_count == 1
    assert integrity.body_breach_count == 0
    assert integrity.close_breach_count == 0


def test_first_anchor_dominance_is_directionally_symmetric():
    bars = _bars([10.0, 10.4, 9.0, 10.8])
    atrs = causal_average_true_range(bars)

    assert dominates_prior_extremes(
        bars, 2, 3, atrs[1], EnvelopeSide.LOWER
    )
    assert not dominates_prior_extremes(
        bars, 3, 3, atrs[2], EnvelopeSide.LOWER
    )
