from datetime import date, timedelta

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.breakout_state import (
    BreakoutDirection,
    BreakoutState,
    evaluate_breakout,
)


def _bars(values: list[tuple[float, float, float, float, int]]) -> list[AnalysisBar]:
    start = date(2026, 1, 1)
    return [
        AnalysisBar(
            start + timedelta(days=index), start + timedelta(days=index),
            value[0], value[1], value[2], value[3], value[4],
            ("test",), False, True, index,
        )
        for index, value in enumerate(values)
    ]


def test_strong_upward_breakout_triggers_and_confirms_on_same_bar():
    bars = _bars([
        (9.8, 10.1, 9.7, 10.0, 100),
        (9.9, 10.1, 9.8, 10.0, 100),
        (10.0, 10.8, 9.95, 10.7, 180),
    ])

    result = evaluate_breakout(
        bars, direction=BreakoutDirection.UP, boundary_price=10,
        invalidation_price=9.5, available_date=bars[0].period_end, preview=False,
    )

    assert result.current_state is BreakoutState.CONFIRMED
    assert [item.state for item in result.transitions] == [
        BreakoutState.TRIGGERED, BreakoutState.CONFIRMED,
    ]
    assert result.trigger_date == result.confirmation_date == bars[-1].period_end


def test_weak_trigger_that_closes_back_inside_is_retained_as_failed():
    bars = _bars([
        (9.9, 10.1, 9.8, 10.0, 100),
        (10.0, 10.2, 9.95, 10.1, 90),
        (10.0, 10.05, 9.8, 9.9, 100),
    ])

    result = evaluate_breakout(
        bars, direction=BreakoutDirection.UP, boundary_price=10,
        invalidation_price=9.5, available_date=bars[0].period_end, preview=False,
    )

    assert result.current_state is BreakoutState.FAILED
    assert result.trigger_date == bars[1].period_end
    assert result.failure_date == bars[2].period_end
    assert "returned inside" in result.transitions[-1].reason


def test_confirmed_breakout_can_retest_then_continue():
    bars = _bars([
        (9.9, 10.1, 9.8, 10.0, 100),
        (10.0, 10.8, 9.95, 10.7, 180),
        (10.2, 10.4, 10.05, 10.2, 80),
        (10.3, 10.7, 10.3, 10.6, 120),
    ])

    result = evaluate_breakout(
        bars, direction=BreakoutDirection.UP, boundary_price=10,
        invalidation_price=9.5, available_date=bars[0].period_end, preview=True,
    )

    assert [item.state for item in result.transitions] == [
        BreakoutState.TRIGGERED, BreakoutState.CONFIRMED,
        BreakoutState.RETESTING, BreakoutState.CONTINUING,
    ]
    assert result.current_state is BreakoutState.CONTINUING
    assert result.preview is True


def test_downward_structure_can_invalidate_before_trigger():
    bars = _bars([(10, 10.3, 9.9, 10.2, 100), (10.3, 10.7, 10.2, 10.6, 100)])

    result = evaluate_breakout(
        bars, direction=BreakoutDirection.DOWN, boundary_price=9.8,
        invalidation_price=10.5, available_date=bars[0].period_end, preview=False,
    )

    assert result.current_state is BreakoutState.INVALIDATED
    assert result.trigger_date is None


def test_trigger_at_first_input_bar_can_time_out_without_follow_through():
    bars = _bars([
        (10.0, 10.2, 9.9, 10.1, 100),
        (10.05, 10.2, 10.0, 10.08, 100),
        (10.02, 10.15, 10.0, 10.07, 100),
        (10.01, 10.12, 10.0, 10.06, 100),
        (10.01, 10.12, 10.0, 10.06, 100),
    ])

    result = evaluate_breakout(
        bars, direction=BreakoutDirection.UP, boundary_price=10,
        invalidation_price=9.5, available_date=bars[0].period_end, preview=False,
    )

    assert result.current_state is BreakoutState.FAILED
    assert result.transitions[-1].reason == "trigger had no timely follow-through"
