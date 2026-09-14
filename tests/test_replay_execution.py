from dataclasses import replace
from datetime import date, timedelta

import pytest

from stock_harness.models import StoredDailyBar
from stock_harness.replay import ExitPlan, FrozenSignal, evaluate_frozen_signal, summarize_evaluations
from stock_harness.replay.signals import signal_from_analysis_result
from stock_harness.replay.execution import evaluate_trade, summarize_trades
from stock_harness.replay.engine import AnalysisReplayEngine


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
    return evaluate_trade(value, bars, bars[0].open if basis == "next_open" else value.reference_close)


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
    evaluation = trade(value, [bar(1, high=10.5, low=8)])
    assert evaluation["net_return"] == .2
    empty = trade(signal(exit_plan=None), [bar(1)])
    summary = summarize_trades([evaluation, empty])
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
    assert evaluate_frozen_signal(value, [bar(1, high=12)])["observation"]["status"] == "target-reached"
    result = evaluate_frozen_signal(value, [bar(1, high=12), bar(2, low=8)])
    assert result["trade"]["net_return"] == .2
    assert result["trade"]["holding_sessions"] == 1


def test_legacy_observation_window_does_not_force_trade_exit():
    value = signal(exit_plan=None, invalidation_price=9, selected_target_price=12,
                   metadata={"maximum_holding_sessions": 1})
    result = evaluate_frozen_signal(value, [bar(1), bar(2, high=12)])
    assert result["observation"]["status"] == "expired-unfulfilled"
    assert result["trade"]["exit_reason"] == "take-profit"
    assert result["trade"]["holding_sessions"] == 2


def test_summary_defaults_to_next_open_and_excludes_unclosed_results():
    closed = evaluate_frozen_signal(signal(), [bar(1, open=11, high=12)])
    pending = evaluate_frozen_signal(signal(), [bar(1)])
    missing = evaluate_frozen_signal(signal(exit_plan=None), [bar(1)])
    unavailable = evaluate_frozen_signal(signal(), [])
    summary = summarize_evaluations([closed, pending, missing, unavailable])
    assert summary["basis"] == "next_open"
    assert summary["trade"]["closed_count"] == 1
    assert summary["trade"]["mean_net_return"] == .090909
    assert summary["trade"]["status_counts"] == {
        "closed": 1, "open-right-censored": 1, "not-configured": 1, "not-entered": 1,
    }


def test_engine_can_exit_after_longest_diagnostic_horizon():
    class Adapter:
        def generate(self, cutoffs):
            return [signal()]

    class Source:
        def future_bars(self, value, sessions):
            assert sessions is None
            return [bar(day) for day in range(1, 121)] + [bar(121, high=12)]

    report = AnalysisReplayEngine(Source()).run(Adapter(), [date(2026, 1, 1)])
    assert report["evaluations"][0]["trade"]["holding_sessions"] == 121
    assert report["summaries"]["next_open"]["overall"]["trade"]["win_rate"] == 1


def test_explicit_system_exit_plan_preserves_cost_and_optional_time_exit():
    value = signal_from_analysis_result({
        "system_id": "trend", "system_version": "v1", "symbol": "TEST",
        "entity_scope": "stock", "opportunity": {
            "exit_plan": {"policy_id": "trend-v1", "stop_price": 9,
                          "target_price": 12, "maximum_holding_sessions": 2,
                          "round_trip_cost_bps": 20},
        },
    }, date(2026, 1, 1), reference_close=10)
    result = evaluate_frozen_signal(value, [bar(1), bar(2)])
    assert result["trade"]["exit_reason"] == "time-exit"
    assert result["trade"]["net_return"] == .048
