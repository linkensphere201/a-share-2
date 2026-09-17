"""M8.23: fixed-input equivalence and bounded shared screening work."""

from dataclasses import replace
from time import perf_counter
from unittest.mock import patch

import pytest

from stock_harness.analysis_inputs import AnalysisInputService
from stock_harness.major_descending_lines import MajorLinePeriod, MajorLineState
from stock_harness.models import DailyBar, Instrument, InstrumentKind
from stock_harness.pattern_analysis import PatternAnalysisService
from stock_harness.screener import (
    ScreenerService, STRATEGY_ID, BULL_FLAG_STRATEGY_ID, PULLBACK_STRATEGY_ID,
    LOW_BASE_STRATEGY_ID, DEEP_DRAWDOWN_STRATEGY_ID, ACCUMULATION_STRATEGY_ID,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore
from test_bull_flag_pattern import fixture as flag_bars
from test_first_pullback import sample as pullback_bars
from test_first_pullback import append as append_pullback_bar
from test_low_base_pullback import bars as low_base_bars
from test_deep_drawdown_pattern import sample as deep_bars
from test_volume_accumulation import _bars as accumulation_bars
from test_screener import _store_with_major_edge


STRATEGIES = [STRATEGY_ID, BULL_FLAG_STRATEGY_ID, PULLBACK_STRATEGY_ID,
              LOW_BASE_STRATEGY_ID, DEEP_DRAWDOWN_STRATEGY_ID, ACCUMULATION_STRATEGY_ID]


def source_bars(strategy):
    if strategy == STRATEGY_ID:
        store, days = _store_with_major_edge()
        try:
            return AnalysisInputService(store).build("000001.SZ", days[-1]).bars
        finally:
            store.close()
    return {BULL_FLAG_STRATEGY_ID: flag_bars, PULLBACK_STRATEGY_ID: pullback_bars,
            LOW_BASE_STRATEGY_ID: low_base_bars, DEEP_DRAWDOWN_STRATEGY_ID: deep_bars,
            ACCUMULATION_STRATEGY_ID: accumulation_bars}[strategy]()


def run_case(strategy, optimized, historical_negatives=False):
    bars = source_bars(strategy)
    with SQLiteMarketDataStore(":memory:") as store:
        instruments = [Instrument(f"{600000+i}.SH", str(i), InstrumentKind.STOCK, "SH")
                       for i in range(20)]
        store.upsert_instruments(instruments)
        daily = []
        for index, instrument in enumerate(instruments):
            for bar in bars:
                if index >= 2:
                    if historical_negatives:
                        if bar is bars[-1]:
                            bar = replace(bar, open=8., high=8.1, low=7.9, close=8., volume=100)
                    else:
                        bar = replace(bar, open=10., high=10.1, low=9.9, close=10., volume=100)
                daily.append(DailyBar(instrument.symbol, bar.period_end, bar.open,
                                      bar.high, bar.low, bar.close, bar.volume))
        store.upsert_daily_bars("tushare", daily)
        store.upsert_trading_dates("tushare", sorted({b.trade_date for b in daily}))
        gate = PatternAnalysisService.analyze_screening_candidate
        summary = SQLiteMarketDataStore.get_analysis_instrument_summary
        with patch.object(PatternAnalysisService, "analyze_screening_candidate",
                          gate if optimized else lambda self, request, *args, **kw: self.analyze(request)[0]), \
             patch.object(SQLiteMarketDataStore, "get_analysis_instrument_summary",
                          summary if optimized else SQLiteMarketDataStore.get_instrument_summary):
            started = perf_counter()
            result = ScreenerService(store).run_sync(
                list(MajorLinePeriod), list(MajorLineState), 20, bars[-1].period_end, strategy,
            )
            elapsed = perf_counter() - started
        assert result["status"] == "succeeded"
        assert result["scanned_count"] == result["universe_count"] == 20
        candidates = store.list_screener_candidates(result["run_id"])
        for candidate in candidates:
            saved = store.get_generated_analysis_run(candidate["analysis_run_id"])
            assert any(item["item_id"] == candidate["line_item_id"] for item in saved["items"])
        return elapsed, [{key: item[key] for key in ("symbol", "state", "score", "evidence")}
                         for item in candidates]


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_all_strategies_preserve_order_scores_and_evidence(strategy):
    before_time, before = run_case(strategy, False)
    after_time, after = run_case(strategy, True)
    assert before and before == after
    print(f"{strategy}: {before_time:.3f}s -> {after_time:.3f}s; "
          f"{before_time / after_time:.2f}x; {len(after)} identical candidates")


def test_strong_pullback_historical_negatives_preserve_results():
    from stock_harness.first_pullback_pattern import detect_first_pullback
    bars = list(pullback_bars())
    bars[-1] = replace(bars[-1], open=8., high=8.1, low=7.9, close=8., volume=100)
    assert detect_first_pullback(bars) is not None
    before_time, before = run_case(PULLBACK_STRATEGY_ID, False, historical_negatives=True)
    after_time, after = run_case(PULLBACK_STRATEGY_ID, True, historical_negatives=True)
    assert len(before) == 2 and before == after
    print(f"strong historical negatives: {before_time:.3f}s -> {after_time:.3f}s; "
          f"{before_time / after_time:.2f}x; identical evidence")


@pytest.mark.parametrize("strategy", STRATEGIES[:3])
def test_negative_stocks_never_register_full_analysis(strategy, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("negative symbols must not run full analysis")

    monkeypatch.setattr(PatternAnalysisService, "analyze", forbidden)
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_instruments([Instrument("600001.SH", "Flat", InstrumentKind.STOCK, "SH")])
        bars = flag_bars()
        store.upsert_daily_bars("tushare", [DailyBar("600001.SH", b.period_end, 10, 10.1, 9.9, 10, 100)
                                             for b in bars])
        result = ScreenerService(store).run_sync(
            list(MajorLinePeriod), list(MajorLineState), 10, bars[-1].period_end, strategy,
        )
        assert result["status"] == "succeeded"
        assert result["candidate_count"] == 0
        assert result["scanned_count"] == 1


def test_stock_input_metadata_is_equivalent_without_ui_queries():
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_instruments([Instrument("600001.SH", "Test", InstrumentKind.STOCK, "SH")])
        store.upsert_daily_bars("tushare", [DailyBar("600001.SH", b.period_end, b.open,
                                                     b.high, b.low, b.close, b.volume) for b in flag_bars()])
        cutoff = flag_bars()[-1].period_end
        expected = AnalysisInputService(store).build("600001.sh", cutoff)
        with patch.object(SQLiteMarketDataStore, "get_analysis_instrument_summary",
                          SQLiteMarketDataStore.get_instrument_summary):
            assert AnalysisInputService(store).build("600001.sh", cutoff) == expected
        statements = []
        store._connection.set_trace_callback(statements.append)
        try:
            summary = store.get_analysis_instrument_summary("600001.sh")
        finally:
            store._connection.set_trace_callback(None)
        assert summary["symbol"] == "600001.SH"
        assert len(statements) == 1
        assert "daily_bars" not in statements[0]
        assert store.get_analysis_instrument_summary("missing") is None


def test_invalidated_pullback_skips_full_analysis(monkeypatch):
    from stock_harness.first_pullback_pattern import detect_first_pullback

    bars = append_pullback_bar(pullback_bars(), 8)
    evidence = detect_first_pullback(bars)
    assert evidence and not evidence["screen_eligible"]
    def forbidden(*args, **kwargs):
        pytest.fail("historical ineligible structures must not run full analysis")
    monkeypatch.setattr(PatternAnalysisService, "analyze", forbidden)
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_instruments([Instrument("600001.SH", "Test", InstrumentKind.STOCK, "SH")])
        store.upsert_daily_bars("tushare", [DailyBar("600001.SH", b.period_end, b.open,
                                                     b.high, b.low, b.close, b.volume) for b in bars])
        result = ScreenerService(store).run_sync([], [], 10, bars[-1].period_end, PULLBACK_STRATEGY_ID)
        assert result["status"] == "succeeded"
        assert result["candidate_count"] == 0


def test_analysis_returns_own_run_not_latest_other_request(monkeypatch):
    from stock_harness.analysis_inputs import AnalysisHorizons, AnalysisTimeframe
    from stock_harness.pattern_analysis import PatternAnalysisRequest
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_instruments([Instrument("600001.SH", "Test", InstrumentKind.STOCK, "SH")])
        bars = flag_bars()
        store.upsert_daily_bars("tushare", [DailyBar("600001.SH", b.period_end, b.open,
                                                     b.high, b.low, b.close, b.volume) for b in bars])
        monkeypatch.setattr(store, "get_latest_generated_analysis_run",
                            lambda *args, **kwargs: pytest.fail("latest can belong to a concurrent request"))
        request = PatternAnalysisRequest("600001.SH", (AnalysisTimeframe.DAILY,),
                                         AnalysisHorizons(), "isolated-request", as_of_date=bars[-1].period_end)
        result, = PatternAnalysisService(store).analyze(request)
        assert result["config_version"] == "isolated-request"
        assert store.get_generated_analysis_run(result["run_id"])["items"] == result["items"]
