from stock_harness.trend_line_engine import (
    TrendSpeedState,
    compare_reanchored_lines,
)


def test_compares_reanchored_lines_with_scale_independent_speed() -> None:
    decelerating = compare_reanchored_lines(
        3466.3817, 58, 3055.6417, 104, 3077.6775, first_index=20,
    )
    assert decelerating.speed_state is TrendSpeedState.DECELERATING
    assert decelerating.slope_change_ratio < -.15

    accelerating = compare_reanchored_lines(
        100.0, 40, 90.0, 80, 60.0, first_index=0,
    )
    assert accelerating.speed_state is TrendSpeedState.ACCELERATING
    assert accelerating.slope_change_ratio > .15


def test_small_reanchor_change_keeps_speed_stable() -> None:
    result = compare_reanchored_lines(
        100.0, 40, 90.0, 80, 80.5, first_index=0,
    )
    assert result.speed_state is TrendSpeedState.STABLE
