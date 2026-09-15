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


@pytest.mark.parametrize("secondary", [False, True])
def test_decline_cache_preserves_full_evidence_and_is_request_local(monkeypatch, secondary: bool) -> None:
    from stock_harness import accumulation_pattern as module

    bars = _secondary_bars() if secondary else _bars()
    original_candidate = module._candidate
    original_metrics = module._decline_metrics
    calls = []

    def metrics(decline, config):
        calls.append((decline[0].period_end, decline[-1].period_end))
        return original_metrics(decline, config)

    monkeypatch.setattr(module, "_decline_metrics", metrics)
    expected = module.detect_accumulation_pattern(bars)
    assert expected is not None
    assert calls and len(calls) == len(set(calls))
    cached_count = len(calls)

    def uncached_candidate(*args, **kwargs):
        kwargs.pop("decline_cache", None)
        return original_candidate(*args, **kwargs)

    monkeypatch.setattr(module, "_candidate", uncached_candidate)
    calls.clear()
    assert module.detect_accumulation_pattern(bars) == expected
    assert len(calls) > cached_count

    monkeypatch.setattr(module, "_candidate", original_candidate)
    calls.clear()
    changed = [replace(b, open=11, close=11, high=11.1, low=10.9) for b in bars]
    assert module.detect_accumulation_pattern(changed) is None
    assert calls and len(calls) == len(set(calls))


def test_screener_adapter_uses_the_shared_pattern_contract() -> None:
    bars = _bars()

    signal = detect_volume_accumulation(bars)

    assert signal is not None
    assert signal.evidence["algorithm_version"] == "decline-platform-accumulation-v4"
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


def _secondary_bars() -> list[AnalysisBar]:
    bars = _bars()[:60]
    rebound = [10.8, 11.1, 11.5, 11.8, 12.2, 12.6, 12.8, 13.0]
    retest = [12.85, 12.7, 12.55, 12.4, 12.3, 12.2, 12.1, 12.0, 12.05, 12.0, 12.05, 12.0]
    platform = [12.08, 12.12, 12.09, 12.14, 12.10, 12.16, 12.11, 12.18, 12.13, 12.20]
    for i, close in enumerate([*rebound, *retest, *platform]):
        volume = 200 if i < 8 else 80 if i < 20 else 160 if i % 2 == 0 else 90
        bars = _append(bars, close, volume)
        bars[-1] = replace(bars[-1], open=close * (1.005 if 8 <= i < 20 or i >= 20 and i % 2 else .995))
    return bars


def test_secondary_base_uses_shared_platform_and_screener_contract() -> None:
    bars = _secondary_bars()
    pattern = detect_accumulation_pattern(bars)
    signal = detect_volume_accumulation(bars)
    assert pattern is not None and pattern.stage == "accumulation"
    assert pattern.evidence["pattern_type"] == "secondary-base"
    assert pattern.evidence["pullback_volume_ratio"] < .9
    assert pattern.evidence["second_bottom_price"] > pattern.evidence["first_bottom_price"]
    assert signal is not None and signal.score == pattern.score
    assert signal.evidence["range_lower"] == pattern.lower
    assert signal.evidence["volume_baseline"] == "pre-platform-retest"


@pytest.mark.parametrize("failure", ["supply", "lower-low", "violent", "no-decline", "one-spike"])
def test_secondary_base_rejects_unhealthy_retests(failure: str) -> None:
    bars = _secondary_bars()
    if failure == "supply":
        bars[68:80] = [replace(b, volume=400) for b in bars[68:80]]
    elif failure == "lower-low":
        bars[74] = replace(bars[74], low=9.5, close=10, open=10.1)
    elif failure == "violent":
        bars[74] = replace(bars[74], open=13.5, high=13.6, close=12.1, low=12)
    elif failure == "no-decline":
        bars[:60] = [replace(b, open=10.7, high=10.8, low=10.6, close=10.7) for b in bars[:60]]
    else:
        bars[-7] = replace(bars[-7], volume=10000)
    assert detect_volume_accumulation(bars) is None


def test_secondary_base_lifecycle_is_causal_and_breakout_is_not_accumulating() -> None:
    bars = _secondary_bars()
    original = detect_accumulation_pattern(bars)
    rally = _append(bars, 13.5, 400)
    pattern = detect_accumulation_pattern(rally)
    assert original is not None and pattern is not None
    assert pattern.stage == "breakout"
    assert detect_volume_accumulation(rally) is None
    assert detect_accumulation_pattern(bars) == original
    failed = detect_accumulation_pattern(_append(rally, 11, 400))
    assert failed is not None and failed.stage == "invalidated"


def test_gentle_retest_can_preserve_a_formed_platform_without_future_bars() -> None:
    from stock_harness.accumulation_pattern import AccumulationPatternConfig, _follow_up
    bars = _secondary_bars()
    original = detect_accumulation_pattern(bars)
    assert original is not None
    latest = _append(bars, 12.12, 70)[-1]
    pattern = _follow_up(original, [latest], latest, None, AccumulationPatternConfig())
    assert pattern.stage == "accumulation"
    assert pattern.evidence["gentle_retest"] is True
    assert pattern.lower == original.lower
    assert original.evidence.get("gentle_retest") is None


def test_secondary_base_remains_scale_invariant() -> None:
    bars = _secondary_bars()
    original = detect_accumulation_pattern(bars)
    scaled = detect_accumulation_pattern([
        replace(b, open=b.open * 10, high=b.high * 10, low=b.low * 10, close=b.close * 10)
        for b in bars
    ])
    assert original is not None and scaled is not None
    assert scaled.stage == original.stage
    assert scaled.score == pytest.approx(original.score)


def test_dry_secondary_platform_requires_internal_demand_not_blanket_expansion() -> None:
    bars = _secondary_bars()
    closes = [12.16, 12.10, 12.18, 12.12, 12.20, 12.14, 12.22, 12.16, 12.24, 12.18]
    bars[-10:] = [replace(
        b, open=close * (.995 if i % 2 == 0 else 1.005),
        high=close * (1.005 if i % 2 == 0 else 1.01), low=close * .99,
        close=close, volume=b.volume // 2,
    ) for i, (b, close) in enumerate(zip(bars[-10:], closes))]
    signal = detect_volume_accumulation(bars)
    assert signal is not None
    assert signal.evidence["demand_regime"] == "dry-up-retest"
    assert .5 <= signal.evidence["platform_volume_ratio"] < 1
    assert signal.evidence["robust_up_down_volume_ratio"] >= 1.1
    bars[-10:] = [replace(b, volume=60) for b in bars[-10:]]
    assert detect_volume_accumulation(bars) is None


@pytest.mark.parametrize("volume,close", [(400, 12.12), (0, 12.12), (70, 11.0)])
def test_followup_cannot_preserve_supply_pressure_missing_volume_or_breakdown(volume: int, close: float) -> None:
    from stock_harness.accumulation_pattern import AccumulationPatternConfig, _follow_up
    bars = _secondary_bars()
    original = detect_accumulation_pattern(bars)
    assert original is not None
    latest = _append(bars, close, volume)[-1]
    pattern = _follow_up(original, [latest], latest, None, AccumulationPatternConfig())
    assert pattern.stage not in {"accumulation", "pending-digestion"}


def test_compact_platform_uses_recent_absolute_limits_and_shared_output() -> None:
    from stock_harness.accumulation_pattern import compact_platform_evidence
    bars = [replace(b, volume=b.volume * 10000) for b in _bars()]
    evidence = compact_platform_evidence(bars)
    assert evidence["qualified"] is True
    signal = detect_volume_accumulation(bars)
    assert signal is not None and signal.evidence["platform_style"] == "compact-platform"
    assert signal.evidence["compact_platform"] == evidence
    # Earlier volatility cannot relax or improve the recent compactness measurement.
    bars[0] = replace(bars[0], high=100)
    assert compact_platform_evidence(bars) == evidence


@pytest.mark.parametrize("failure", ["body", "wick", "gap", "drift", "inactive"])
def test_compact_platform_rejects_recent_disorder(failure: str) -> None:
    from stock_harness.accumulation_pattern import compact_platform_evidence
    bars = [replace(b, volume=b.volume * 10000) for b in _bars()]
    if failure == "body":
        bars[-2] = replace(bars[-2], open=10.6, low=10.5)
    elif failure == "wick":
        bars[-2] = replace(bars[-2], high=12.5)
    elif failure == "gap":
        bars[-2] = replace(bars[-2], open=11.9, close=11.91, high=11.92, low=11.89)
    elif failure == "drift":
        bars[-1] = replace(bars[-1], open=11.8, close=11.81, high=11.85, low=11.75)
    else:
        bars[-1] = replace(bars[-1], volume=0)
    assert compact_platform_evidence(bars)["qualified"] is False


def test_compact_rank_precedes_broad_bases_and_recognition_is_a_bounded_bonus() -> None:
    from stock_harness.volume_accumulation import VolumeAccumulationSignal, accumulation_rank_key
    def signal(quality: float, recognized: bool, compact: bool = True) -> VolumeAccumulationSignal:
        return VolumeAccumulationSignal(99, {
            "platform_style": "compact-platform" if compact else "broad-base",
            "compact_platform": {"score": quality},
            "recognition": {"tags": ["recent"] if recognized else []},
        })
    assert accumulation_rank_key(signal(70, True)) > accumulation_rank_key(signal(73, False))
    assert accumulation_rank_key(signal(70, True)) < accumulation_rank_key(signal(80, False))
    assert accumulation_rank_key(signal(70, False)) > accumulation_rank_key(signal(99, True, False))
