from dataclasses import replace
import sqlite3

import pytest

from stock_harness.background_reads import background_reader
from stock_harness.models import AdjustmentFactor
from stock_harness.sqlite_store import SQLiteMarketDataStore
from test_prepared_analysis_input import seed


def test_background_reader_never_migrates_and_closes_on_failure(tmp_path, monkeypatch):
    with SQLiteMarketDataStore(tmp_path / "fixture.sqlite") as store:
        seed(store)
        def fail(*args):
            raise AssertionError("read-only worker must not initialize schema")
        monkeypatch.setattr("stock_harness.sqlite_initialization.initialize_database", fail)
        with pytest.raises(RuntimeError, match="worker failure"):
            with background_reader(store) as reader:
                assert reader is not store and reader.read_only
                assert reader.get_latest_daily_bar_date("600001.SH")
                raise RuntimeError("worker failure")
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            reader.get_latest_daily_bar_date("600001.SH")
        assert store.get_latest_daily_bar_date("600001.SH")


def test_memory_reader_keeps_source_owned_by_caller():
    with SQLiteMarketDataStore(":memory:") as store:
        seed(store)
        with background_reader(store) as reader:
            assert reader is store
        assert store.get_latest_daily_bar_date("600001.SH")


def test_bars_and_factors_share_one_bounded_read_snapshot(tmp_path, monkeypatch):
    with SQLiteMarketDataStore(tmp_path / "fixture.sqlite") as store:
        bars = seed(store)
        store.upsert_adjustment_factors("tushare", [AdjustmentFactor(bar.symbol, bar.trade_date, 1) for bar in bars])
        with background_reader(store) as reader:
            original = reader.get_recent_daily_bars_many
            def interleaved(*args):
                value = original(*args)
                store.upsert_daily_bars("tushare", [replace(bars[-1], close=20, high=21)])
                store.upsert_adjustment_factors("tushare", [AdjustmentFactor(bars[-1].symbol, bars[-1].trade_date, 2)])
                return value
            monkeypatch.setattr(reader, "get_recent_daily_bars_many", interleaved)
            value, basis = reader.get_recent_causally_adjusted_stock_bars_many([bars[-1].symbol], bars[-1].trade_date, 80)
            assert value[bars[-1].symbol][0].close == 10
            assert value[bars[-1].symbol][-1].close == 10
            assert not reader._connection.in_transaction
            monkeypatch.setattr(reader, "get_recent_daily_bars_many", original)
            updated, _ = reader.get_recent_causally_adjusted_stock_bars_many([bars[-1].symbol], bars[-1].trade_date, 80)
            assert updated[bars[-1].symbol][0].close == 5
            assert updated[bars[-1].symbol][-1].close == 20
