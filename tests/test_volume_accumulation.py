from datetime import date, timedelta

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.accumulation_pattern import detect_accumulation_pattern
from stock_harness.volume_accumulation import detect_volume_accumulation


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
    assert signal.evidence["algorithm_version"] == "decline-platform-accumulation-v1"
    assert signal.evidence["range_lower"] < signal.evidence["range_upper"]


def test_limit_up_is_visible_evidence_not_a_rejection() -> None:
    bars = _bars()
    limit_date = bars[-3].period_end.isoformat()

    signal = detect_volume_accumulation(bars, limit_up_dates=frozenset({limit_date}))

    assert signal is not None
    assert signal.evidence["limit_up_count"] == 1
    assert signal.evidence["limit_up_policy"] == "accepted-not-required"


def test_rejects_a_platform_that_breaks_the_raised_bottom() -> None:
    assert detect_accumulation_pattern(_bars(break_platform=True)) is None

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
