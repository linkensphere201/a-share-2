from datetime import date, timedelta

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.major_descending_lines import (
    MajorLinePeriod,
    MajorLineState,
    _classify_state,
    _upper_envelope_integrity,
    detect_major_descending_lines,
)
from stock_harness.trend_pivots import causal_average_true_range


def _bars(closes: list[float], highs: list[float] | None = None, volumes: list[int] | None = None):
    start = date(2025, 1, 1)
    highs = highs or [value + 0.2 for value in closes]
    volumes = volumes or [100] * len(closes)
    return tuple(AnalysisBar(
        start + timedelta(days=index), start + timedelta(days=index),
        close, high, min(close, high) - 0.2, close, volumes[index],
        ("test",), False, True, 0,
    ) for index, (close, high) in enumerate(zip(closes, highs)))


def _major_fixture(latest: list[float]):
    closes = [10.0] * 250
    highs = [10.2] * 250
    for index in range(250):
        boundary = 18 - (4.2 / 95) * (index - 20)
        closes[index] = boundary - 1.5
        highs[index] = boundary - 1.2
    closes[:20] = [16.0] * 20
    highs[:20] = [16.4] * 20
    highs[20], closes[20] = 18.0, 17.3
    highs[115], closes[115] = 13.8, 13.1
    highs[205], closes[205] = 9.82, 9.2
    closes[-len(latest):] = latest
    for offset, close in enumerate(latest, len(closes) - len(latest)):
        highs[offset] = close + 0.12
    return _bars(closes, highs, [100] * 245 + [100, 110, 130, 120, 90])


def test_detects_year_line_with_stable_identity_and_no_future_dependency():
    bars = _major_fixture([7.7, 7.7, 7.7, 7.75, 7.8])
    detected = detect_major_descending_lines(bars, [MajorLinePeriod.YEAR])
    assert detected
    assert detected[0].item_id.startswith("major-descending-1y-")
    assert detected[0].code == "MDL-1Y-01"
    assert detected[0].independent_touch_count >= 1
    assert detected[0].wick_breach_count == 0
    assert detected[0].close_breach_count == 0
    assert "trade_scenario" not in detected[0].__dataclass_fields__
    before = detect_major_descending_lines(bars[:-1], [MajorLinePeriod.YEAR])
    mutated = (*bars[:-1], _bars([20.0])[0])
    assert before == detect_major_descending_lines(mutated[:-1], [MajorLinePeriod.YEAR])


def test_classifies_breakout_retest_after_recent_close_through():
    bars = _major_fixture([8.0, 7.85, 7.95, 8.2, 7.9])
    detected = detect_major_descending_lines(bars, [MajorLinePeriod.YEAR])
    assert any(item.state is MajorLineState.BREAKOUT_RETEST for item in detected)


def test_rejects_short_anchor_span_from_major_line_period():
    bars = _major_fixture([7.8, 7.9, 8.0, 8.1, 8.2])
    highs = [item.high for item in bars]
    highs[220], highs[240] = 9.0, 8.0
    altered = tuple(
        AnalysisBar(
            item.period_start, item.period_end, item.open, highs[index],
            min(item.low, highs[index]), item.close, item.volume, item.sources,
            item.contains_provisional, item.period_complete, item.observed_at_ms,
        ) for index, item in enumerate(bars)
    )
    values = detect_major_descending_lines(altered, [MajorLinePeriod.YEAR])
    assert all(item.anchor_span_bars >= 180 for item in values)


def test_rejects_line_that_cuts_through_intermediate_price_structure():
    bars = list(_major_fixture([8.0, 7.85, 7.95, 8.2, 7.9]))
    item = bars[150]
    bars[150] = AnalysisBar(
        item.period_start, item.period_end, 24.0, 26.8, 23.5, 25.5,
        item.volume, item.sources, item.contains_provisional,
        item.period_complete, item.observed_at_ms,
    )

    values = detect_major_descending_lines(tuple(bars), [MajorLinePeriod.YEAR])

    assert all(not (item.first_index == 20 and item.second_index == 205) for item in values)


def test_rejects_two_anchor_line_without_independent_confirmation_contact():
    bars = list(_major_fixture([8.0, 7.85, 7.95, 8.2, 7.9]))
    item = bars[115]
    bars[115] = AnalysisBar(
        item.period_start, item.period_end, item.open, item.high - 1.0,
        item.low, item.close, item.volume, item.sources,
        item.contains_provisional, item.period_complete, item.observed_at_ms,
    )

    assert not detect_major_descending_lines(tuple(bars), [MajorLinePeriod.YEAR])


def test_separates_wick_and_close_breaches_using_prior_bar_atr():
    bars = list(_major_fixture([8.0, 7.85, 7.95, 8.2, 7.9]))
    first, second, evaluated = 20, 205, 150
    slope = (bars[second].high - bars[first].high) / (second - first)
    boundary = bars[first].high + slope * (evaluated - first)
    original = bars[evaluated]
    bars[evaluated] = AnalysisBar(
        original.period_start, original.period_end, boundary - 0.8,
        boundary + 2.0, boundary - 1.0, boundary - 0.3,
        original.volume, original.sources, False, True, 0,
    )
    atrs = causal_average_true_range(bars)
    pivots = [20, 115, 150, 205]

    wick_only = _upper_envelope_integrity(
        bars, atrs, first, second, slope, 244, pivots, 8,
    )
    assert wick_only.wick_breach_count == 1
    assert wick_only.body_breach_count == 0
    assert wick_only.close_breach_count == 0

    item = bars[evaluated]
    bars[evaluated] = AnalysisBar(
        item.period_start, item.period_end, boundary + 1.0, item.high,
        item.low, boundary - 0.3, item.volume, item.sources, False, True, 0,
    )
    body_breach = _upper_envelope_integrity(
        bars, causal_average_true_range(bars), first, second, slope, 244,
        pivots, 8,
    )
    assert body_breach.wick_breach_count == 1
    assert body_breach.body_breach_count == 1
    assert body_breach.close_breach_count == 0

    item = bars[evaluated]
    bars[evaluated] = AnalysisBar(
        item.period_start, item.period_end, item.open, item.high,
        item.low, boundary + 1.0, item.volume, item.sources, False, True, 0,
    )
    close_breach = _upper_envelope_integrity(
        bars, causal_average_true_range(bars), first, second, slope, 244,
        pivots, 8,
    )
    assert close_breach.wick_breach_count == 1
    assert close_breach.close_breach_count == 1


def test_rejects_fixed_002875_v1_anchor_shape_after_dominant_intermediate_high():
    clean = _annil_v1_shape(False)
    escaped = _annil_v1_shape(True)

    assert any(
        item.first_date == "2025-10-16"
        and item.second_date == "2026-08-12"
        for item in detect_major_descending_lines(clean, [MajorLinePeriod.YEAR])
    )
    assert all(
        not (
            item.first_date == "2025-10-16"
            and item.second_date == "2026-08-12"
        )
        for item in detect_major_descending_lines(escaped, [MajorLinePeriod.YEAR])
    )


def _annil_v1_shape(with_intermediate_escape: bool) -> tuple[AnalysisBar, ...]:
    first, second = 34, 235
    first_price, second_price = 20.33, 14.54
    slope = (second_price - first_price) / (second - first)
    first_date = date(2025, 10, 16)
    second_date = date(2026, 8, 12)
    end_date = date(2026, 9, 1)
    result = []
    for index in range(250):
        if index <= first:
            current_date = first_date - timedelta(days=first - index)
        elif index <= second:
            elapsed = round((second_date - first_date).days * (index - first) / (second - first))
            current_date = first_date + timedelta(days=elapsed)
        else:
            elapsed = round((end_date - second_date).days * (index - second) / (249 - second))
            current_date = second_date + timedelta(days=elapsed)
        boundary = first_price + slope * (index - first)
        high = boundary - 1.2
        close = boundary - 1.5
        if index < first:
            high, close = 19.0, 18.6
        if index in (first, 130, second):
            high, close = boundary, boundary - 0.7
        if with_intermediate_escape and index == 160:
            high, close = 26.8, 25.5
        if index == 248:
            high, close = 14.49, 14.49
        if index == 249:
            high, close = 14.86, 14.15
        result.append(AnalysisBar(
            current_date, current_date, close, high, close - 0.3, close,
            1_000_000, ("tushare",), False, True, 0,
        ))
    return tuple(result)


def _state_bars(closes: list[float], lows: list[float] | None = None):
    start = date(2026, 1, 1)
    lows = lows or [value - 0.2 for value in closes]
    return tuple(AnalysisBar(
        start + timedelta(days=index), start + timedelta(days=index),
        close, max(close, 12 - index * 0.1), lows[index], close,
        100, ("test",), False, True, 0,
    ) for index, close in enumerate(closes))


def test_classifies_critical_broken_retest_and_rejects_failed_breakout():
    boundaries = [12 - index * 0.1 for index in range(20)]

    critical = _state_bars([value * 0.99 for value in boundaries])
    assert _classify_state(critical, 0, -0.1)[0] is MajorLineState.CRITICAL_BREAKOUT

    broken_closes = [value * 0.99 for value in boundaries]
    broken_closes[17:] = [boundaries[index] * 1.02 for index in range(17, 20)]
    broken = _state_bars(broken_closes, [value * 1.015 for value in boundaries])
    assert _classify_state(broken, 0, -0.1)[0] is MajorLineState.BROKEN_OUT

    retest_closes = broken_closes[:]
    retest_closes[-1] = boundaries[-1] * 1.004
    retest_lows = [value * 1.015 for value in boundaries]
    retest_lows[-1] = boundaries[-1] * 1.001
    assert _classify_state(_state_bars(retest_closes, retest_lows), 0, -0.1)[0] is MajorLineState.BREAKOUT_RETEST

    failed_closes = broken_closes[:]
    failed_closes[-1] = boundaries[-1] * 0.96
    assert _classify_state(_state_bars(failed_closes), 0, -0.1)[0] is None
