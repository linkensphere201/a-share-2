from dataclasses import replace
from datetime import date

from stock_harness.futures_continuous_build import build_futures_continuous_series
from stock_harness.models import (
    FuturesBarState,
    FuturesContinuousSeries,
    FuturesContract,
    FuturesDailyBar,
    FuturesExchange,
    FuturesLifecycleStatus,
    FuturesPriceBasis,
    FuturesProduct,
    FuturesRollMapping,
    FuturesSeriesKind,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore


def _fixture():
    product = FuturesProduct(
        "FUTPROD:SHFE:CU", "CU", "Copper", FuturesExchange.SHFE,
        None, 5, "contract", "CNY/tonne",
    )
    first = FuturesContract(
        "FUT:SHFE:CU:202609", "CU2609.SHF", product.symbol, "Copper 2609",
        FuturesExchange.SHFE, "202609", date(2025, 9, 1), date(2026, 9, 15),
        date(2026, 9, 18), None, 5, "contract", "CNY/tonne",
        FuturesLifecycleStatus.TRADING,
    )
    second = replace(
        first, symbol="FUT:SHFE:CU:202610", provider_symbol="CU2610.SHF",
        contract_month="202610", display_name="Copper 2610",
        last_trading_date=date(2026, 10, 15), delivery_date=date(2026, 10, 20),
    )
    series = FuturesContinuousSeries(
        "FUTCONT:SHFE:CU:MAIN:raw", "CU.SHF", product.symbol, "Copper main",
        FuturesExchange.SHFE, FuturesSeriesKind.MAIN, "MAIN",
        FuturesPriceBasis.RAW, "mapping-v1",
    )
    return product, first, second, series


def _bar(symbol, day, close):
    return FuturesDailyBar(
        symbol, day, day, close - 1, close + 1, close - 2, close,
        close - 1, close, close - 1, 100, 1000, 200, 1, None,
        "tushare-futures", FuturesBarState.FINAL,
    )


def test_builds_new_series_then_skips_clean_and_rebuilds_dirty_raw_suffix():
    product, first, second, series = _fixture()
    first_day, roll_day, next_day = date(2026, 8, 19), date(2026, 8, 20), date(2026, 8, 21)
    mappings = [
        FuturesRollMapping(series.symbol, series.provider_symbol, first_day,
                           first.symbol, first.provider_symbol),
        FuturesRollMapping(series.symbol, series.provider_symbol, roll_day,
                           second.symbol, second.provider_symbol),
    ]
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [first, second], [series]
        )
        store.upsert_futures_roll_mappings("tushare-futures", mappings)
        store.upsert_futures_daily_bars("tushare-futures", [
            _bar(first.symbol, first_day, 100),
            _bar(first.symbol, roll_day, 101),
            _bar(second.symbol, roll_day, 105),
            _bar(second.symbol, next_day, 106),
        ])
        built = build_futures_continuous_series(
            store, "tushare-futures", first_day, next_day
        )
        clean = build_futures_continuous_series(
            store, "tushare-futures", first_day, next_day
        )
        store.upsert_futures_daily_bars("tushare-futures", [
            _bar(second.symbol, next_day, 108)
        ])
        rebuilt = build_futures_continuous_series(
            store, "tushare-futures", first_day, next_day
        )
        rows = store.list_futures_daily_bars(series.symbol, first_day, next_day)
    assert (built.completed_series, built.rows_written) == (1, 3)
    assert clean.skipped_clean_series == 1
    assert (rebuilt.completed_series, rebuilt.rows_written) == (1, 1)
    assert rows[-1].close == 108


def test_skips_unmapped_series_without_failing_other_work():
    product, first, second, series = _fixture()
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(
            "tushare-futures", [product], [first, second], [series]
        )
        result = build_futures_continuous_series(
            store, "tushare-futures", date(2026, 8, 1), date(2026, 8, 21)
        )
    assert result.skipped_unmapped_series == 1
    assert result.errors == ()
