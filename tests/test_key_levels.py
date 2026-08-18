from datetime import date, timedelta

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.key_levels import (
    KeyLevelConfig,
    detect_horizontal_levels,
    estimate_clear_space,
    estimate_daily_volume_profile,
)
from stock_harness.trend_pivots import PivotKind, PricePivot


def _bars(count: int = 12) -> list[AnalysisBar]:
    start = date(2026, 1, 1)
    return [
        AnalysisBar(
            period_start=start + timedelta(days=index),
            period_end=start + timedelta(days=index),
            open=10.2 + index * 0.02,
            high=11.0 + index * 0.02,
            low=10.0 + index * 0.02,
            close=10.5 + index * 0.02,
            volume=1000 if index < 8 else 300,
            sources=("test",), contains_provisional=False,
            period_complete=True, observed_at_ms=index,
        )
        for index in range(count)
    ]


def _pivot(index: int, price: float) -> PricePivot:
    day = date(2026, 1, 1) + timedelta(days=index)
    return PricePivot(PivotKind.LOW, day, price, day + timedelta(days=1), 0.5, False)


def test_clusters_repeated_confirmed_pivots_into_auditable_level():
    levels = detect_horizontal_levels(
        _bars(), [_pivot(0, 10.0), _pivot(4, 10.04), _pivot(8, 10.08)]
    )

    assert len(levels) == 1
    assert levels[0].observation_count == 3
    assert levels[0].sources == ("pivot-low",)
    assert levels[0].lower < levels[0].center < levels[0].upper
    assert levels[0].score_components["touches"] > 0
    assert levels[0].role_reversal is False


def test_daily_volume_profile_is_explicitly_estimated_and_bounded():
    profile = estimate_daily_volume_profile(
        _bars(), KeyLevelConfig(volume_bins=12, dense_bin_quantile=0.6)
    )

    assert profile.zones
    assert profile.total_estimated_volume > 0
    assert profile.bin_width > 0
    assert all(0 < zone.estimated_share <= 1 for zone in profile.zones)
    assert all("estimated" in zone.uncertainty for zone in profile.zones)
    assert sum(zone.estimated_volume for zone in profile.zones) <= (
        profile.total_estimated_volume + 1e-6
    )


def test_zero_volume_input_produces_no_false_cost_zone():
    bars = [bar.__class__(
        bar.period_start, bar.period_end, bar.open, bar.high, bar.low, bar.close,
        0, bar.sources, bar.contains_provisional, bar.period_complete, bar.observed_at_ms,
    ) for bar in _bars()]

    assert estimate_daily_volume_profile(bars).zones == ()


def test_clear_space_reports_nearest_upper_and_lower_estimates():
    levels = detect_horizontal_levels(
        _bars(), [_pivot(0, 10.0), _pivot(4, 10.04), _pivot(8, 10.08)]
    )
    profile = estimate_daily_volume_profile(
        _bars(), KeyLevelConfig(volume_bins=12, dense_bin_quantile=0.6)
    )

    result = estimate_clear_space(10.5, levels, profile.zones)

    assert result["lower_price"] is not None
    assert result["lower_percent"] is not None


def test_level_score_records_volume_confluence_without_claiming_exact_cost():
    bars = _bars()
    profile = estimate_daily_volume_profile(
        bars, KeyLevelConfig(volume_bins=12, dense_bin_quantile=0.6)
    )
    levels = detect_horizontal_levels(
        bars,
        [_pivot(0, 10.0), _pivot(4, 10.04), _pivot(8, 10.08)],
        volume_zones=profile.zones,
    )

    assert levels[0].volume_confluence >= 0
    assert "volume_confluence" in levels[0].score_components


def test_level_marks_support_resistance_role_reversal():
    low = _pivot(0, 10.0)
    high_day = date(2026, 1, 5)
    high = PricePivot(
        PivotKind.HIGH, high_day, 10.05, high_day + timedelta(days=1), 0.5, False
    )

    levels = detect_horizontal_levels(_bars(), [low, high])

    assert levels[0].role_reversal is True
    assert levels[0].score_components["role_reversal"] == 1


def test_confirmed_breakout_and_retest_feed_back_into_level_evidence():
    start = date(2026, 2, 1)
    closes = [9.7, 9.8, 10.2, 10.05, 10.3]
    bars = [
        AnalysisBar(
            start + timedelta(days=index), start + timedelta(days=index),
            close, close + 0.12, close - 0.12, close, 100,
            ("test",), False, True, index,
        )
        for index, close in enumerate(closes)
    ]
    pivot = PricePivot(
        PivotKind.HIGH, start, 10.0, start + timedelta(days=1), 0.5, False
    )

    levels = detect_horizontal_levels(bars, [pivot])

    assert len(levels) == 1
    assert "breakout-up" in levels[0].sources
    assert "retest-support" in levels[0].sources
    assert levels[0].role_reversal is True
    assert start + timedelta(days=2) in levels[0].evidence_dates
    assert start + timedelta(days=3) in levels[0].evidence_dates


def test_cross_without_a_later_hold_does_not_fabricate_retest_source():
    start = date(2026, 3, 1)
    closes = [9.7, 10.2, 10.4]
    bars = [
        AnalysisBar(
            start + timedelta(days=index), start + timedelta(days=index),
            close, close + 0.05, close - 0.05, close, 100,
            ("test",), False, True, index,
        )
        for index, close in enumerate(closes)
    ]
    pivot = PricePivot(
        PivotKind.HIGH, start, 10.0, start + timedelta(days=1), 0.5, False
    )

    level = detect_horizontal_levels(bars, [pivot])[0]

    assert "breakout-up" in level.sources
    assert "retest-support" not in level.sources
