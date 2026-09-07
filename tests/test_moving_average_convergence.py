from datetime import date, timedelta

from stock_harness.analysis_inputs import AnalysisBar, AnalysisTimeframe
from stock_harness.analysis_results import GeneratedItemType, validate_items
from stock_harness.generated_patterns import generate_pattern_items
from stock_harness.moving_average_convergence import (
    MovingAverageConvergenceConfig,
    MovingAverageConvergenceState,
    convergence_config,
    detect_moving_average_convergence,
)
from stock_harness.trend_lines_analysis import TrendHorizon


def _bars(closes: list[float], *, final_volume: int = 100) -> list[AnalysisBar]:
    start = date(2026, 1, 1)
    return [
        AnalysisBar(
            start + timedelta(days=index), start + timedelta(days=index),
            close, close + 0.4, close - 0.4, close,
            final_volume if index == len(closes) - 1 else 100,
            ("test",), False, True, index,
        )
        for index, close in enumerate(closes)
    ]


def _config() -> MovingAverageConvergenceConfig:
    return MovingAverageConvergenceConfig(
        periods=(5, 10, 20),
        maximum_spread_percent=0.008,
        maximum_spread_atr=1.25,
    )


def test_detects_persistent_compressed_moving_averages() -> None:
    pattern = detect_moving_average_convergence(
        _bars([100 + (0.1 if index % 2 else -0.1) for index in range(40)]),
        _config(),
    )

    assert pattern is not None
    assert pattern.state in {
        MovingAverageConvergenceState.CONVERGING,
        MovingAverageConvergenceState.COMPRESSED,
    }
    assert pattern.direction == "neutral"
    assert pattern.compressed_bars >= 3
    assert pattern.spread_percent < 0.008
    assert pattern.available_date <= pattern.end_date


def test_detects_ordered_bullish_release_from_a_recent_compressed_band() -> None:
    pattern = detect_moving_average_convergence(
        _bars([100.0] * 35 + [101.0, 103.0, 106.0], final_volume=220),
        _config(),
    )

    assert pattern is not None
    assert pattern.state is MovingAverageConvergenceState.BULLISH_EXPANSION
    assert pattern.direction == "bullish"
    assert pattern.event_date == pattern.end_date
    assert pattern.current_values[0] > pattern.current_values[1] > pattern.current_values[2]
    assert pattern.volume_ratio == 2.2


def test_detects_ordered_bearish_release_from_a_recent_compressed_band() -> None:
    pattern = detect_moving_average_convergence(
        _bars([100.0] * 35 + [99.0, 97.0, 94.0]),
        _config(),
    )

    assert pattern is not None
    assert pattern.state is MovingAverageConvergenceState.BEARISH_EXPANSION
    assert pattern.direction == "bearish"
    assert pattern.event_date == pattern.end_date
    assert pattern.current_values[0] < pattern.current_values[1] < pattern.current_values[2]


def test_rejects_insufficient_history_and_unordered_release() -> None:
    assert detect_moving_average_convergence(_bars([100.0] * 25), _config()) is None
    assert detect_moving_average_convergence(
        _bars([100.0] * 35 + [106.0, 94.0, 101.0]), _config()
    ) is None


def test_price_spread_threshold_has_a_deterministic_boundary() -> None:
    bars = _bars([100 + index * 0.03 for index in range(40)])
    accepted = MovingAverageConvergenceConfig(
        periods=(5, 10, 20), maximum_spread_percent=0.003,
    )
    rejected = MovingAverageConvergenceConfig(
        periods=(5, 10, 20), maximum_spread_percent=0.002,
    )

    assert detect_moving_average_convergence(bars, accepted) is not None
    assert detect_moving_average_convergence(bars, rejected) is None


def test_horizon_profiles_have_explicit_increasing_periods() -> None:
    assert convergence_config(TrendHorizon.SHORT, 0.02).periods == (5, 10, 20)
    assert convergence_config(TrendHorizon.MEDIUM, 0.02).periods == (5, 20, 60)
    assert convergence_config(TrendHorizon.LONG, 0.02).periods == (20, 60, 120)


def test_generated_pattern_contains_auditable_band_and_child_evidence() -> None:
    bars = _bars([100.0] * 35 + [101.0, 103.0, 106.0], final_volume=220)
    items = generate_pattern_items(
        bars, (), TrendHorizon.SHORT, AnalysisTimeframe.DAILY, preview=False
    )
    validate_items(items)
    pattern = next(
        item for item in items
        if item.payload.get("pattern_type") == "moving-average-convergence"
    )
    children = [item for item in items if item.parent_item_id == pattern.item_id]

    assert pattern.item_type is GeneratedItemType.PATTERN
    assert pattern.item_id == "short-pattern-moving-average-convergence-0"
    assert pattern.payload["completion_state"] == "confirmed"
    assert pattern.payload["boundary_geometry"]["kind"] == "moving-average-band"
    assert len(pattern.payload["boundary_geometry"]["points"]) >= 4
    assert pattern.payload["ma_periods"] == [5, 10, 20]
    assert {item.payload.get("kind") for item in children} == {
        "moving-average-convergence-summary",
        "latest-structural-event-summary",
    }


def test_result_at_a_cutoff_is_unchanged_by_unseen_future_bars() -> None:
    prefix = _bars([100.0] * 35 + [101.0, 103.0, 106.0])
    frozen = detect_moving_average_convergence(prefix, _config())
    extended = _bars([100.0] * 35 + [101.0, 103.0, 106.0, 110.0, 112.0])

    assert frozen == detect_moving_average_convergence(extended[:len(prefix)], _config())
