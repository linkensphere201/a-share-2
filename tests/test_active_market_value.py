from datetime import date

import pytest

from stock_harness.active_market_value import DEFAULT_SYMBOL
from stock_harness.models import ActiveMarketValueFeature, DailyBar, Instrument, InstrumentKind
from stock_harness.sqlite_store import SQLiteMarketDataStore


def feature(symbol: str, trade_date: date, turnover: float, free_share: float, close: float):
    return ActiveMarketValueFeature(
        symbol, trade_date, turnover, free_share,
        free_share * close, free_share * close * 1.5, close,
    )


def test_materializes_causal_active_market_value_bars_and_coverage():
    store = SQLiteMarketDataStore(":memory:")
    first = date(2026, 9, 3)
    second = date(2026, 9, 4)
    store.upsert_instruments([
        Instrument("000001.SZ", "A", InstrumentKind.STOCK, "SZ"),
        Instrument("600000.SH", "B", InstrumentKind.STOCK, "SH"),
    ])
    store.upsert_daily_bars("tushare", [
        DailyBar("000001.SZ", first, 9, 11, 8, 10, 100),
        DailyBar("600000.SH", first, 19, 21, 18, 20, 100),
        DailyBar("000001.SZ", second, 10, 12, 9, 11, 100),
    ])
    store.upsert_active_market_value_features("tushare", first, [
        feature("000001.SZ", first, 2, 1_000, 10),
        feature("600000.SH", first, 1, 2_000, 20),
    ])
    store.upsert_active_market_value_features("tushare", second, [
        feature("000001.SZ", second, 3, 1_000, 11),
        feature("600000.SH", second, 1, 2_000, 20),
    ])

    result = store.build_active_market_value_index(first, second)
    diagnostics = store.list_active_market_value_diagnostics(first, second)
    chart = store.get_daily_bars(DEFAULT_SYMBOL)
    store.close()

    assert result["status"] == "ready"
    assert result["rows"] == 2
    assert result["base_date"] == first
    assert result["latest_change_percent"] is not None
    assert diagnostics[0]["coverage_ratio"] == pytest.approx(1)
    assert diagnostics[1]["coverage_ratio"] == pytest.approx(11_000 / 51_000)
    assert diagnostics[0]["absolute_high"] >= diagnostics[0]["absolute_close"]
    assert diagnostics[0]["absolute_low"] <= diagnostics[0]["absolute_close"]
    assert diagnostics[0]["close"] == pytest.approx(1000)
    assert [item.close for item in chart] == pytest.approx([
        item["close"] for item in diagnostics
    ])


def test_incremental_build_appends_with_stable_base_and_prior_ema_state():
    store = SQLiteMarketDataStore(":memory:")
    first = date(2026, 9, 3)
    second = date(2026, 9, 4)
    store.upsert_instruments([
        Instrument("000001.SZ", "A", InstrumentKind.STOCK, "SZ"),
    ])
    store.upsert_daily_bars("tushare", [
        DailyBar("000001.SZ", first, 9, 11, 8, 10, 100),
    ])
    store.upsert_active_market_value_features("tushare", first, [
        feature("000001.SZ", first, 1, 1_000, 10),
    ])
    store.build_active_market_value_index(first, first)
    first_close = store.get_daily_bars(DEFAULT_SYMBOL)[0].close

    store.upsert_daily_bars("tushare", [
        DailyBar("000001.SZ", second, 10, 12, 9, 11, 100),
    ])
    store.upsert_active_market_value_features("tushare", second, [
        feature("000001.SZ", second, 3, 1_000, 11),
    ])
    result = store.build_active_market_value_index(mode="incremental")
    chart = store.get_daily_bars(DEFAULT_SYMBOL)

    assert result["rows"] == 2
    assert result["base_date"] == first
    assert chart[0].close == pytest.approx(first_close)
    assert chart[1].close > chart[0].close
    store.close()


def test_feature_receipts_are_resumable_and_unknown_symbols_are_reported():
    store = SQLiteMarketDataStore(":memory:")
    trade_date = date(2026, 9, 4)
    store.upsert_instruments([
        Instrument("000001.SZ", "A", InstrumentKind.STOCK, "SZ"),
    ])
    result = store.upsert_active_market_value_features("tushare", trade_date, [
        feature("000001.SZ", trade_date, 2, 1_000, 10),
        feature("999999.SH", trade_date, 2, 1_000, 10),
    ])
    coverage = store.active_market_value_feature_coverage()

    assert result["row_count"] == 1
    assert result["skipped_count"] == 1
    assert store.has_active_market_value_feature_receipt("tushare", trade_date)
    assert coverage["rows"] == 1
    store.close()


def test_build_is_causal_when_future_features_are_already_stored():
    store = SQLiteMarketDataStore(":memory:")
    days = [date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4)]
    store.upsert_instruments([
        Instrument("000001.SZ", "A", InstrumentKind.STOCK, "SZ"),
    ])
    for offset, trade_date in enumerate(days):
        close = 10 + offset
        store.upsert_daily_bars("tushare", [
            DailyBar("000001.SZ", trade_date, close - 1, close + 1, close - 2, close, 100),
        ])
        store.upsert_active_market_value_features("tushare", trade_date, [
            feature("000001.SZ", trade_date, 1 + offset, 1_000, close),
        ])

    result = store.build_active_market_value_index(days[0], days[1])
    before = store.list_active_market_value_diagnostics(days[0], days[1])
    assert result["rows"] == 2
    assert result["last_trade_date"] == days[1]

    store.build_active_market_value_index(mode="incremental")
    after = store.list_active_market_value_diagnostics(days[0], days[2])
    assert after[:2] == before
    assert len(after) == 3
    store.close()


def test_empty_summary_and_invalid_mode_are_explicit():
    store = SQLiteMarketDataStore(":memory:")
    assert store.get_active_market_value_index() == {
        "status": "missing", "symbol": DEFAULT_SYMBOL, "rows": 0,
    }
    with pytest.raises(ValueError, match="invalid active-market-value build mode"):
        store.build_active_market_value_index(mode="correction")
    store.close()
