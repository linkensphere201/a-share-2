from dataclasses import replace
from datetime import date, timedelta

import pytest

from stock_harness.models import StoredDailyBar
from stock_harness.replay import ExitPlan, FrozenSignal, evaluate_frozen_signal, summarize_evaluations
from stock_harness.replay.signals import signal_from_analysis_result


def signal(**kwargs):
    return replace(FrozenSignal(
        system_id="mean-reversion", system_version="v5", symbol="TEST", scope="stock",
        signal_date=date(2026, 1, 1), direction="long", reference_close=10,
        exit_plan=ExitPlan("mean-center-v1", 9, 12),
    ), **kwargs)


def bar(day, open=10, high=11, low=9.5, close=10.5):
    return StoredDailyBar(
        symbol="TEST", trade_date=date(2026, 1, 1) + timedelta(days=day),
        open=open, high=high, low=low, close=close, volume=1000,
        source="test", updated_at_ms=0,
    )


def trade(value, bars, basis="reference_close"):
    return evaluate_frozen_signal(value, bars)[basis]["trade"]


def test_target_exit_is_frozen_even_when_price_crashes_later():
    first = bar(1, high=12.5, close=12)
    outcome = trade(signal(), [first])
    assert outcome == trade(signal(), [first, bar(2, open=5, high=6, low=4, close=5)])
    assert outcome["net_return"] == .2
    assert outcome["holding_sessions"] == 1
    assert outcome["exit_reason"] == "take-profit"


def test_gap_stop_uses_open_not_unreachable_stop_price():
    outcome = trade(signal(), [bar(1, open=8, high=8.5, low=7, close=8)])
    assert outcome["exit_price"] == 8
    assert outcome["net_return"] == -.2
    assert outcome["exit_reason"] == "stop-loss-gap"


def test_ambiguous_day_is_explicit_and_stop_first():
    outcome = trade(signal(), [bar(1, high=13, low=8)])
    assert outcome["same_session_ambiguous"]
    assert outcome["exit_price"] == 9


def test_open_gap_resolves_order_before_intraday_range():
    outcome = trade(signal(), [bar(1, open=13, high=14, low=8)])
    assert outcome["exit_price"] == 13
    assert not outcome["same_session_ambiguous"]


def test_next_open_outside_plan_does_not_create_a_trade():
    outcome = trade(signal(), [bar(1, open=13, high=14, low=11)], "next_open")
    assert outcome["status"] == "not-entered"


def test_time_exit_and_costs_and_open_positions():
    value = signal(exit_plan=ExitPlan("time-v1", 9, 12, 2, 20))
    assert trade(value, [bar(1)])["status"] == "open-right-censored"
    outcome = trade(value, [bar(1), bar(2)])
    assert outcome["exit_reason"] == "time-exit"
    assert outcome["net_return"] == .048
    assert outcome["net_r_multiple"] == .48


def test_short_and_missing_boundaries_and_summary():
    value = signal(direction="short", exit_plan=ExitPlan("short-v1", 11, 8))
    evaluation = evaluate_frozen_signal(value, [bar(1, high=10.5, low=8)])
    assert evaluation["reference_close"]["trade"]["net_return"] == .2
    empty = evaluate_frozen_signal(signal(exit_plan=None), [bar(1)])
    summary = summarize_evaluations([evaluation, empty])["trade"]
    assert summary["closed_count"] == 1
    assert summary["win_rate"] == 1
    assert summary["status_counts"]["not-configured"] == 1


@pytest.mark.parametrize("system", ["trend", "mean-reversion"])
def test_complete_results_use_one_execution_contract(system):
    value = signal_from_analysis_result({
        "system_id": system, "system_version": "v1", "symbol": "TEST",
        "entity_scope": "board", "opportunity": {
            "direction": "long", "invalidation_price": 9,
            "selected_target_price": 12,
        },
    }, date(2026, 1, 1), reference_close=10)
    assert trade(value, [bar(1, high=12)])["net_return"] == .2
