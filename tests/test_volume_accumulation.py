from datetime import date, timedelta
from dataclasses import replace

import pytest

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.accumulation_pattern import detect_accumulation_pattern
from stock_harness.volume_accumulation import detect_volume_accumulation


def _append(bars: list[AnalysisBar], close: float, volume: int = 130) -> list[AnalysisBar]:
    day = bars[-1].period_end + timedelta(days=1)
    return [*bars, replace(
        bars[-1], period_start=day, period_end=day, open=close * .995,
        high=close * 1.01, low=close * .99, close=close, volume=volume,
    )]


def _bars(*, break_platform: bool = False) -> list[AnalysisBar]:
    start = date(2026, 1, 1)
    decline = [16.0 - index * .09 for index in range(60)]
    lift = [10.6, 10.55, 10.5, 10.48, 10.5, 11.05, 11.1, 11.12, 11.15, 11.18]
    platform = [11.2, 11.3, 11.15, 11.28, 11.18, 11.32, 11.22, 11.35, 11.25, 11.38]
    if break_platform:
        platform[-1] = 9.8
    closes = decline + lift + platform
    volumes = [100] * 70 + [150, 105, 155, 100, 160, 105, 165, 100, 170, 105]
    return [
        AnalysisBar(
            period_start=start + timedelta(days=index),
            period_end=start + timedelta(days=index),
            open=close * (0.995 if index % 2 == 0 else 1.005),
            high=close * 1.01,
            low=close * 0.99,
            close=close,
            volume=volume,
            sources=("test",),
            contains_provisional=False,
            period_complete=True,
            observed_at_ms=0,
        )
        for index, (volume, close) in enumerate(zip(volumes, closes))
    ]


def test_detects_decline_lift_and_demand_led_platform() -> None:
    bars = _bars()

    pattern = detect_accumulation_pattern(bars)

    assert pattern is not None
    assert pattern.start_date == bars[-10].period_start.isoformat()
    assert pattern.evidence["downward_ma_stack"] is True
    assert 4 <= pattern.evidence["bottom_lift_percent"] <= 12
    assert pattern.evidence["small_body_sessions"] == 10
    assert pattern.evidence["up_down_volume_ratio"] > 1


def test_screener_adapter_uses_the_shared_pattern_contract() -> None:
    bars = _bars()

    signal = detect_volume_accumulation(bars)

    assert signal is not None
    assert signal.evidence["algorithm_version"] == "decline-platform-accumulation-v2"
    assert signal.evidence["range_lower"] < signal.evidence["range_upper"]


def test_limit_up_is_visible_evidence_not_a_rejection() -> None:
    bars = _bars()
    limit_date = bars[-3].period_end.isoformat()

    signal = detect_volume_accumulation(bars, limit_up_dates=frozenset({limit_date}))

    assert signal is not None
    assert signal.evidence["limit_up_count"] == 1
    assert signal.evidence["limit_up_policy"] == "accepted-not-required"


def test_rejects_a_platform_that_breaks_the_raised_bottom() -> None:
    pattern = detect_accumulation_pattern(_bars(break_platform=True))
    assert pattern is not None
    assert pattern.stage == "invalidated"
    assert detect_volume_accumulation(_bars(break_platform=True)) is None

def test_rejects_history_without_a_medium_term_decline() -> None:
    bars = _bars()
    flat = [
        AnalysisBar(
            period_start=item.period_start, period_end=item.period_end,
            open=11, high=11.1, low=10.9, close=11, volume=item.volume,
            sources=item.sources, contains_provisional=False,
            period_complete=True, observed_at_ms=0,
        )
        for item in bars[:60]
    ]
    assert detect_accumulation_pattern([*flat, *bars[60:]]) is None


def test_atr_and_robust_boundaries_ignore_one_lower_wick() -> None:
    bars = _bars()
    baseline = detect_accumulation_pattern(bars)
    bars[-5] = replace(bars[-5], low=bars[-5].low * .8)
    pattern = detect_accumulation_pattern(bars)
    assert pattern is not None and baseline is not None
    assert pattern.lower > pattern.evidence["extreme_low"]
    assert abs(pattern.lower - baseline.lower) < .2


def test_dynamic_platform_can_extend_beyond_ten_sessions() -> None:
    bars = _bars()
    for i in range(6):
        bars = _append(bars, 11.3 + .03 * (i % 2), 150)
    pattern = detect_accumulation_pattern(bars)
    assert pattern is not None
    assert 10 < pattern.evidence["platform_sessions"] <= 20
    assert pattern.stage == "accumulation"


def test_lifecycle_preserves_base_on_breakout_then_invalidates_failed_breakout() -> None:
    bars = _bars()
    original = detect_accumulation_pattern(bars)
    rally = _append(bars, 12.5, 220)
    breakout = detect_accumulation_pattern(rally)
    assert original is not None and breakout is not None
    assert breakout.stage == "breakout"
    assert (breakout.lower, breakout.upper, breakout.end_date) == (
        original.lower, original.upper, original.end_date,
    )
    assert detect_volume_accumulation(rally) is None
    failure = detect_accumulation_pattern(_append(rally, 10.8, 240))
    assert failure is not None and failure.stage == "invalidated"
    assert detect_accumulation_pattern(bars) == original


def test_no_confirmation_before_limit_up_followup_exists() -> None:
    bars = _bars()
    dates = frozenset({bars[-1].period_end.isoformat()})
    pending = detect_accumulation_pattern(bars, limit_up_dates=dates)
    assert pending is not None and pending.stage == "pending-digestion"
    assert pending.evidence["limit_up_events"][0]["observed_sessions"] == 0
    digested = detect_accumulation_pattern(
        _append(_append(bars, 11.3, 95), 11.35, 100), limit_up_dates=dates,
    )
    assert digested is not None
    assert digested.evidence["limit_up_events"][0]["state"] == "digested"
    assert detect_accumulation_pattern(bars, limit_up_dates=dates) == pending


def test_single_huge_session_cannot_supply_demand_confirmation() -> None:
    bars = _bars()
    # Place it before every eligible endpoint so shortening the range cannot hide it.
    bars[-7] = replace(bars[-7], volume=10000, open=bars[-7].close * .995)
    assert detect_volume_accumulation(bars) is None


def test_more_red_days_do_not_replace_average_bullish_volume_strength() -> None:
    bars = _bars()
    for i in range(70, 80):
        red = i % 5 != 0
        bars[i] = replace(bars[i], open=bars[i].close * (.995 if red else 1.005),
                          volume=110 if red else 300)
    assert detect_volume_accumulation(bars) is None


def test_missing_auxiliary_data_is_explicit_and_does_not_zero_quality() -> None:
    bars = _bars()
    missing = detect_accumulation_pattern(bars)
    supplied = detect_accumulation_pattern(
        bars, limit_up_dates=frozenset(),
        turnover_by_date={b.period_end.isoformat(): 1.5 for b in bars},
    )
    assert missing is not None and supplied is not None
    assert missing.score == supplied.score
    assert missing.evidence["evidence_completeness"] < supplied.evidence["evidence_completeness"]
    assert supplied.evidence["turnover_ratio"] == 1
    assert sum(supplied.evidence["score_components"].values()) == pytest.approx(supplied.score)


@pytest.mark.parametrize("change", [
    {"contains_provisional": True}, {"period_complete": False},
    {"volume": float("nan")}, {"close": 0}, {"contains_roll_event": True},
])
def test_invalid_or_unfinished_inputs_cannot_form_a_range(change: dict) -> None:
    bars = _bars()
    bars[-1] = replace(bars[-1], **change)
    assert detect_accumulation_pattern(bars) is None


def test_price_rescaling_does_not_change_stage_or_score() -> None:
    bars = _bars()
    original = detect_accumulation_pattern(bars)
    scaled = detect_accumulation_pattern([
        replace(b, open=b.open * 10, high=b.high * 10, low=b.low * 10, close=b.close * 10)
        for b in bars
    ])
    assert original is not None and scaled is not None
    assert scaled.stage == original.stage
    assert scaled.score == pytest.approx(original.score)
    assert scaled.lower == pytest.approx(original.lower * 10)


@pytest.mark.parametrize("factor", [.4, 2.5])
def test_lift_outside_five_to_ten_percent_is_scored_in_atr_units(factor: float) -> None:
    bars = _bars()
    def scale(value: float) -> float:
        return 10.5 + factor * (value - 10.5)
    bars[60:] = [replace(b, open=scale(b.open), high=scale(b.high), low=scale(b.low),
                         close=scale(b.close)) for b in bars[60:]]
    pattern = detect_accumulation_pattern(bars)
    assert pattern is not None and pattern.stage == "accumulation"
    assert not 5 <= pattern.evidence["bottom_lift_percent"] <= 10


def test_deteriorating_platform_cannot_be_rescued_by_an_older_valid_window() -> None:
    bars = _append(_bars(), 11.3, 10000)
    pattern = detect_accumulation_pattern(bars)
    assert pattern is not None and pattern.stage == "stabilizing"
    assert detect_volume_accumulation(bars) is None


def test_platform_with_falling_price_center_cannot_be_accumulation() -> None:
    bars = _bars()
    for i in range(70, 80):
        close = 11.5 - (i - 70) * .05
        bars[i] = replace(bars[i], open=close * .995, close=close,
                          high=close * 1.01, low=close * .99)
    assert detect_volume_accumulation(bars) is None


def test_early_lift_limit_up_uses_event_support_not_the_later_raised_platform() -> None:
    bars = _bars()
    pattern = detect_accumulation_pattern(
        bars, limit_up_dates=frozenset({bars[-20].period_end.isoformat()}),
    )
    assert pattern is not None and pattern.stage == "accumulation"
    event = pattern.evidence["limit_up_events"][0]
    assert event["position"] == "lift"
    assert event["state"] == "digested"
    assert event["support_price"] < pattern.lower
