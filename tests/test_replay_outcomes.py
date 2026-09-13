from __future__ import annotations

from datetime import date, timedelta

import pytest

from stock_harness.models import StoredDailyBar
from stock_harness.mean_reversion_replay import _analyze, select_replay_dates
from stock_harness.replay import (
    AnalysisReplayEngine,
    DEFAULT_EVALUATION_HORIZONS,
    FrozenSignal,
    evaluate_frozen_signal,
    summarize_evaluations,
    summarize_evaluations_by,
)


def test_default_effect_horizons_are_shared_10_60_120() -> None:
    assert DEFAULT_EVALUATION_HORIZONS == (10, 60, 120)


def test_common_evaluator_reports_close_mfe_mae_r_and_censoring() -> None:
    signal = FrozenSignal(
        system_id="test-system", system_version="v1", symbol="TEST",
        scope="stock", signal_date=date(2026, 1, 1), direction="long",
        reference_close=10, invalidation_price=9, selected_target_price=12,
    )
    bars = _bars(60)

    result = evaluate_frozen_signal(signal, bars)

    ten = result["reference_close"]["horizons"]["10"]
    assert ten["status"] == "complete"
    assert ten["close_return"] == pytest.approx(.1)
    assert ten["mfe"] == pytest.approx(.11)
    assert ten["mae"] == pytest.approx(-.02)
    assert ten["mfe_r"] == pytest.approx(1.1)
    assert result["reference_close"]["first_boundary_event"] == "target-first"
    assert result["reference_close"]["first_boundary_event_session"] == 19
    assert result["reference_close"]["horizons"]["60"]["status"] == "complete"
    assert result["reference_close"]["horizons"]["120"]["status"] == "right-censored"
    assert result["next_open"]["entry_price"] == pytest.approx(10.0)


def test_common_summary_never_counts_right_censoring_as_failure() -> None:
    signal = FrozenSignal(
        system_id="trend", system_version="v1", symbol="TEST", scope="board",
        signal_date=date(2026, 1, 1), direction="long", reference_close=10,
    )
    evaluation = evaluate_frozen_signal(signal, _bars(10))

    summary = summarize_evaluations([evaluation])

    assert summary["horizons"]["10"]["complete_count"] == 1
    assert summary["horizons"]["10"]["positive_close_rate"] == 1
    assert summary["horizons"]["60"]["complete_count"] == 0
    assert summary["horizons"]["60"]["right_censored_count"] == 1
    assert summary["horizons"]["60"]["positive_close_rate"] is None


def test_common_evaluator_normalizes_short_direction_and_ambiguous_boundaries() -> None:
    signal = FrozenSignal(
        system_id="short-system", system_version="v1", symbol="TEST",
        scope="stock", signal_date=date(2026, 1, 1), direction="short",
        reference_close=10, invalidation_price=11, selected_target_price=9,
    )
    bars = _bars(10)
    bars[0] = StoredDailyBar(
        symbol="TEST", trade_date=bars[0].trade_date,
        open=10, high=11.2, low=8.8, close=9.5,
        volume=1000, source="test", updated_at_ms=0,
    )

    result = evaluate_frozen_signal(signal, bars)
    ten = result["reference_close"]["horizons"]["10"]

    assert result["reference_close"]["first_boundary_event"] == "same-session-ambiguous"
    assert ten["mfe"] == pytest.approx(.12)
    assert ten["mae"] == pytest.approx(-.12)


def test_common_grouping_changes_only_the_breakdown_not_metric_definitions() -> None:
    stock = FrozenSignal(
        system_id="trend", system_version="v1", symbol="S", scope="stock",
        signal_date=date(2026, 1, 1), direction="long", reference_close=10,
        setup_family="breakout",
    )
    board = FrozenSignal(
        system_id="mean", system_version="v1", symbol="B", scope="board",
        signal_date=date(2026, 1, 1), direction="long", reference_close=10,
        setup_family="pullback",
    )
    evaluations = [
        evaluate_frozen_signal(stock, _bars(10)),
        evaluate_frozen_signal(board, _bars(10)),
    ]

    grouped = summarize_evaluations_by(evaluations, "scope")

    assert set(grouped) == {"board", "stock"}
    assert grouped["board"]["horizons"].keys() == {"10", "60", "120"}
    assert grouped["stock"]["horizons"].keys() == {"10", "60", "120"}


def test_replay_engine_accepts_different_system_adapters_with_one_evaluator() -> None:
    cutoff = date(2026, 1, 1)
    source = _FutureSource(_bars(120))
    engine = AnalysisReplayEngine(source)

    trend = engine.run(_Adapter("trend", cutoff), [cutoff])
    mean = engine.run(_Adapter("mean-reversion", cutoff), [cutoff])

    assert trend["evaluations"][0]["contract_version"] == mean["evaluations"][0]["contract_version"]
    assert trend["summaries"]["reference_close"]["overall"]["horizons"].keys() == {"10", "60", "120"}
    assert mean["summaries"]["reference_close"]["overall"]["horizons"].keys() == {"10", "60", "120"}
    assert trend["summaries"]["reference_close"]["by_scope"].keys() == {"stock"}


def _bars(count: int) -> list[StoredDailyBar]:
    start = date(2026, 1, 1)
    return [StoredDailyBar(
        symbol="TEST", trade_date=start + timedelta(days=index + 1),
        open=10 + index * .1, high=10.2 + index * .1,
        low=9.8 + index * .1, close=10.1 + index * .1,
        volume=1000, source="test", updated_at_ms=0,
    ) for index in range(count)]


class _Adapter:
    def __init__(self, system_id: str, cutoff: date) -> None:
        self._system_id = system_id
        self._cutoff = cutoff

    def generate(self, cutoffs):
        assert tuple(cutoffs) == (self._cutoff,)
        return [FrozenSignal(
            system_id=self._system_id, system_version="v1", symbol="TEST",
            scope="stock", signal_date=self._cutoff, direction="long",
            reference_close=10,
        )]


class _FutureSource:
    def __init__(self, bars):
        self._bars = bars

    def future_bars(self, signal, sessions):
        return self._bars[:sessions]


def test_fast_mean_reversion_adapter_preserves_production_opportunity_gates() -> None:
    closes = [100 + index * .35 for index in range(125)]
    closes.extend([144 - index * .55 for index in range(14)])
    closes.extend([136.6, 136.8, 137.0, 138.2])
    start = date(2025, 1, 1)
    bars = [StoredDailyBar(
        symbol="TEST", trade_date=start + timedelta(days=index),
        open=close - .2,
        high=close + (.3 if index == len(closes) - 1 else .6),
        low=close - .6, close=close,
        volume=1400 if index == len(closes) - 1 else 1000,
        source="test", updated_at_ms=0,
    ) for index, close in enumerate(closes)]
    counters = __import__("collections").Counter()

    signal = _analyze("TEST", "stock", bars, bars[-1].trade_date, counters)

    assert signal is None
    assert counters["stock:family:directional-pullback"] == 1
    assert counters["stock:state:reversal-confirmed"] == 1
    assert counters["stock:rejected:no-credible-target-at-3r"] == 1
    assert counters["stock:rejected:invalid-long-price-ordering"] == 1


def test_replay_dates_prefer_completed_benchmark_bars_over_sparse_calendar() -> None:
    dates = [date(2026, 1, 1) + timedelta(days=index) for index in range(4)]

    class Store:
        def get_daily_bars(self, symbol, start_date, end_date):
            assert symbol == "000001.SH"
            return [_bars(1)[0].__class__(
                symbol=symbol, trade_date=value, open=10, high=10,
                low=10, close=10, volume=1, source="test", updated_at_ms=0,
            ) for value in dates]

        def list_trading_dates(self, source, start_date, end_date):
            raise AssertionError("calendar fallback should not be used")

    assert select_replay_dates(Store(), dates[-1], 3) == dates[-3:]
