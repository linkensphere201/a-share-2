from dataclasses import replace
from datetime import date, timedelta
import sqlite3

import pytest

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.deep_drawdown_pattern import (
    KIND, STATE, STRATEGY_ID, REFERENCE_CLOSES, REFERENCE_VOLUMES, detect_deep_drawdown,
)
from stock_harness.models import DailyBar, Instrument, InstrumentKind
from stock_harness.pattern_analysis import PatternAnalysisService
from stock_harness.screener import ScreenerService
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.sqlite_schema import SCHEMA


def sample():
    end = date(2026, 9, 16)
    return tuple(AnalysisBar(
        period_start=end - timedelta(days=59-i), period_end=end - timedelta(days=59-i),
        open=c, high=c * 1.01, low=c * .99, close=c, volume=v,
        sources=("frozen-reference-test",), contains_provisional=False,
        period_complete=True, observed_at_ms=0,
    ) for i, (c, v) in enumerate(zip(REFERENCE_CLOSES, REFERENCE_VOLUMES)))


def test_fixed_reference_and_score_scale():
    assert len(REFERENCE_CLOSES) == len(REFERENCE_VOLUMES) == 60
    assert REFERENCE_CLOSES[-1] == 7.45  # Not the 8.20 limit-up close.
    result = detect_deep_drawdown(sample())
    assert result["score"] == 100
    assert result["reference"]["end_date"] == "2026-09-15"
    assert result["similarity_components"] == {
        "price_path": 50., "recent_path": 20., "amplitude": 15., "volume_path": 15.,
    }
    assert result["recent_early_volume_ratio"] == pytest.approx(.139, abs=.001)
    assert result["close_range_20d_percent"] == pytest.approx(6.31, abs=.01)
    scaled = [replace(b, open=b.open*7, high=b.high*7, low=b.low*7,
                      close=b.close*7, volume=b.volume*13) for b in sample()]
    assert detect_deep_drawdown(scaled)["score"] == 100


@pytest.mark.parametrize("expanding", [False, True])
def test_matching_prices_cannot_compensate_for_noncontracting_volume(expanding):
    values = [replace(b, volume=100 + (i * 20 if expanding else 0))
              for i, b in enumerate(sample())]
    assert detect_deep_drawdown(values) is None


@pytest.mark.parametrize("gap", ["outside", "inside", "truncated"])
def test_missing_bar_gate_is_scoped_to_known_comparison_window(gap):
    from stock_harness.analysis_inputs import AnalysisInputService, AnalysisTimeframe, AnalysisInputMode, AnalysisInputWarning
    from stock_harness.trend_analysis import _deep_drawdown_item
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_instruments([Instrument("000001.SZ", "test", InstrumentKind.STOCK, "SZ")])
        store.upsert_daily_bars("tushare", [DailyBar("000001.SZ", b.period_end,
            b.open, b.high, b.low, b.close, b.volume) for b in sample()])
        value = AnalysisInputService(store).build("000001.SZ", date(2026, 9, 16),
            AnalysisTimeframe.DAILY, AnalysisInputMode.FINAL)
        missing = sample()[0].period_start - timedelta(days=5) if gap != "inside" else sample()[20].period_start
        warning = AnalysisInputWarning("unexplained_missing_bars", "warning", "test",
            dates=(missing,), count=12 if gap == "truncated" else 1)
        item = _deep_drawdown_item(replace(value, warnings=(warning,)))
        assert (item is not None) == (gap == "outside")


@pytest.mark.parametrize("case", ["short", "flat", "rising", "zero-volume", "nan", "preview",
                                  "incomplete", "roll", "unsorted", "before-reference"])
def test_invalid_or_unlike_controls(case):
    values = list(sample())
    if case == "short": values.pop()
    if case in ("flat", "rising"):
        values = [replace(b, open=10+i*(case == "rising"), close=10+i*(case == "rising"),
                          high=11+i*(case == "rising"), low=9+i*(case == "rising"))
                  for i, b in enumerate(values)]
    if case == "zero-volume": values[-1] = replace(values[-1], volume=0)
    if case == "nan": values[-1] = replace(values[-1], close=float("nan"))
    if case == "preview": values[-1] = replace(values[-1], contains_provisional=True)
    if case == "incomplete": values[-1] = replace(values[-1], period_complete=False)
    if case == "roll": values[-1] = replace(values[-1], contains_roll_event=True)
    if case == "unsorted": values.reverse()
    if case == "before-reference":
        values = [replace(b, period_start=b.period_start-timedelta(days=2),
                          period_end=b.period_end-timedelta(days=2)) for b in values]
    assert detect_deep_drawdown(values) is None


@pytest.mark.parametrize("future", [False, True])
def test_saved_screener_chart_parity_and_future_isolation(future, monkeypatch):
    calls = []
    original = PatternAnalysisService.analyze
    def counted(self, request):
        calls.append(request.symbol)
        return original(self, request)
    monkeypatch.setattr(PatternAnalysisService, "analyze", counted)
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_instruments([Instrument(s, s, InstrumentKind.STOCK, "SZ")
                                  for s in ("000001.SZ", "000002.SZ", "002137.SZ", "000003.SZ")])
        for s in ("000001.SZ", "000002.SZ", "002137.SZ"):
            rows = [DailyBar(s, b.period_end, b.open, b.high, b.low, b.close, b.volume)
                    for b in sample()]
            if future:
                rows.append(DailyBar(s, date(2026, 9, 17), 1., 1000., .1, 999., 10**12))
            store.upsert_daily_bars("tushare", rows)
        store.upsert_trading_dates("tushare", [b.period_end for b in sample()])
        run = ScreenerService(store).run_sync([], [], 10, date(2026, 9, 16), STRATEGY_ID)
        candidates = store.list_screener_candidates(run["run_id"])
        assert [c["symbol"] for c in candidates] == ["000001.SZ", "000002.SZ"]
        assert calls == ["000001.SZ", "000002.SZ"]
        assert run["scanned_count"] == run["universe_count"] == 4
        for c in candidates:
            assert c["score"] == 100 and c["state"] == STATE
            saved = store.get_generated_analysis_run(c["analysis_run_id"])
            zone = next(i for i in saved["items"] if i["item_id"] == c["line_item_id"])
            assert zone["payload"] == c["evidence"]
            assert zone["payload"]["kind"] == KIND
            assert zone["payload"]["as_of_date"] == "2026-09-16"
            assert c["evidence"]["first_target_price"] is None


def test_new_strategy_state_migrates_previous_schema(tmp_path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA.replace(", 'shape-match'", ""))
    with SQLiteMarketDataStore(path) as store:
        schema = store._connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='screener_candidates'"
        ).fetchone()[0]
        assert "'shape-match'" in schema
        assert store._connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_api_registers_named_strategy_without_template_options():
    from stock_harness.api_models import ScreenerRunInput
    assert ScreenerRunInput(strategy_id=STRATEGY_ID).strategy_id == STRATEGY_ID
    definition = next(s for s in ScreenerService.strategies() if s["strategy_id"] == STRATEGY_ID)
    assert definition["name"] == "深跌缩量整理"
    assert definition["window"] == 60
