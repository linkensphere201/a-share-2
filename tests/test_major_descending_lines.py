from datetime import date, timedelta

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.major_descending_lines import (
    MajorLinePeriod,
    MajorLineState,
    _classify_state,
    detect_major_descending_lines,
)


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
        closes[index] = boundary - 0.8
        highs[index] = boundary - 0.55
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
    assert all(item.anchor_span_bars >= 80 for item in values)


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
