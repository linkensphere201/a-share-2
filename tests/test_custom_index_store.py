from datetime import date

import pytest

from stock_harness.models import (
    AdjustmentFactor, DailyBar, Instrument, InstrumentKind, StockTradeStatus,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore


def _prepared_store() -> SQLiteMarketDataStore:
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("000001.SZ", "Ping An Bank", InstrumentKind.STOCK, "SZ"),
        Instrument("600000.SH", "SPD Bank", InstrumentKind.STOCK, "SH"),
    ])
    dates = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)]
    store.upsert_trading_dates("tushare", dates)
    store.upsert_daily_bars("tushare", [
        DailyBar("000001.SZ", dates[0], 10, 10, 10, 10, 100),
        DailyBar("600000.SH", dates[0], 20, 20, 20, 20, 200),
        DailyBar("000001.SZ", dates[1], 10, 12, 9, 11, 110),
        DailyBar("600000.SH", dates[1], 20, 22, 18, 21, 210),
        DailyBar("000001.SZ", dates[2], 11, 13, 10, 12, 120),
        DailyBar("600000.SH", dates[2], 21, 23, 20, 22, 220),
    ])
    store.upsert_adjustment_factors("tushare", [
        AdjustmentFactor(symbol, trade_date, 1.0)
        for symbol in ("000001.SZ", "600000.SH")
        for trade_date in dates
    ])
    return store


def test_materialized_history_is_read_through_the_normal_bar_path():
    with _prepared_store() as store:
        created = store.create_custom_index(
            "test-index", "Bank Pair", "materialized test", date(2026, 8, 3),
            1000, "equal", [{"symbol": "000001.SZ"}, {"symbol": "600000.SH"}],
        )
        assert created["status"] == "pending"

        rebuilt = store.rebuild_custom_index("test-index")
        bars = store.get_daily_bars("CINDEX:TEST-INDEX")
        summary = store.get_instrument_summary("CINDEX:TEST-INDEX")

        assert rebuilt["status"] == "ready"
        assert len(bars) == 3
        assert bars[0].close == 1000
        assert bars[1].close == pytest.approx(1075)
        assert bars[1].source == "local_custom_index"
        assert [item.volume for item in bars] == [300, 320, 340]
        assert summary is not None
        assert summary["classification"] == "custom-index"
        assert summary["rows"] == 3


def test_materialized_history_uses_bar_dates_when_calendar_is_only_partially_backfilled():
    with SQLiteMarketDataStore(":memory:") as store:
        days = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)]
        store.upsert_instruments([
            Instrument("000001.SZ", "Ping An Bank", InstrumentKind.STOCK, "SZ"),
        ])
        store.upsert_trading_dates("tushare", [days[-1]])
        store.upsert_daily_bars("tushare", [
            DailyBar("000001.SZ", days[0], 10, 10, 9, 10, 100),
            DailyBar("000001.SZ", days[1], 10, 11, 10, 11, 110),
            DailyBar("000001.SZ", days[2], 11, 12, 11, 12, 120),
        ])
        store.upsert_adjustment_factors("tushare", [
            AdjustmentFactor("000001.SZ", day, 1) for day in days
        ])
        store.create_custom_index(
            "partial-calendar", "Partial Calendar", "", days[0], 1000, "equal",
            [{"symbol": "000001.SZ"}],
        )

        rebuilt = store.rebuild_custom_index("partial-calendar")
        bars = store.get_daily_bars("CINDEX:PARTIAL-CALENDAR")

        assert rebuilt["rows"] == 3
        assert [item.trade_date for item in bars] == days


def test_reopening_store_reads_materialized_rows_without_recalculation(tmp_path):
    path = tmp_path / "market.sqlite"
    with SQLiteMarketDataStore(path) as store:
        store.upsert_instruments([
            Instrument("000001.SZ", "Ping An Bank", InstrumentKind.STOCK, "SZ")
        ])
        days = [date(2026, 8, 3), date(2026, 8, 4)]
        store.upsert_trading_dates("tushare", days)
        store.upsert_daily_bars("tushare", [
            DailyBar("000001.SZ", days[0], 10, 10, 10, 10, 100),
            DailyBar("000001.SZ", days[1], 10, 11, 9, 11, 110),
        ])
        store.upsert_adjustment_factors("tushare", [
            AdjustmentFactor("000001.SZ", day, 1) for day in days
        ])
        store.create_custom_index(
            "persisted", "Persisted", "", days[0], 1000, "equal",
            [{"symbol": "000001.SZ"}],
        )
        store.rebuild_custom_index("persisted")

    with SQLiteMarketDataStore(path) as reopened:
        bars = reopened.get_daily_bars("CINDEX:PERSISTED")
        assert [item.close for item in bars] == [1000, 1100]
        assert reopened.get_custom_index("persisted")["status"] == "ready"


def test_custom_index_delete_cleans_snapshot_and_instrument():
    with _prepared_store() as store:
        store.create_custom_index(
            "delete-me", "Delete Me", "", date(2026, 8, 3), 1000, "equal",
            [{"symbol": "000001.SZ"}],
        )
        store.rebuild_custom_index("delete-me")

        assert store.delete_custom_index("delete-me") is True
        assert store.get_custom_index("delete-me") is None
        assert store.get_instrument_summary("CINDEX:DELETE-ME") is None


def test_incremental_rebuild_appends_only_new_final_dates():
    with _prepared_store() as store:
        store.create_custom_index(
            "incremental", "Incremental", "", date(2026, 8, 3), 1000, "equal",
            [{"symbol": "000001.SZ"}, {"symbol": "600000.SH"}],
        )
        store.rebuild_custom_index("incremental")
        original = store.get_daily_bars("CINDEX:INCREMENTAL")
        next_day = date(2026, 8, 6)
        store.upsert_trading_dates("tushare", [next_day])
        store.upsert_daily_bars("tushare", [
            DailyBar("000001.SZ", next_day, 12, 13, 11, 13, 130),
            DailyBar("600000.SH", next_day, 22, 24, 21, 23, 230),
        ])
        store.upsert_adjustment_factors("tushare", [
            AdjustmentFactor("000001.SZ", next_day, 1),
            AdjustmentFactor("600000.SH", next_day, 1),
        ])

        rebuilt = store.rebuild_custom_index("incremental", mode="incremental")
        current = store.get_daily_bars("CINDEX:INCREMENTAL")

        assert rebuilt["rows"] == 4
        assert len(current) == 4
        assert current[-1].trade_date == next_day
        assert [item.updated_at_ms for item in current[:3]] == [
            item.updated_at_ms for item in original
        ]
        run = store._connection.execute(
            """
            SELECT mode, row_count FROM custom_index_calculation_runs
            WHERE index_id = 'incremental' ORDER BY run_id DESC LIMIT 1
            """
        ).fetchone()
        assert tuple(run) == ("incremental", 1)


def test_incremental_rebuild_requires_explicit_suspension_evidence():
    with _prepared_store() as store:
        store.create_custom_index(
            "suspension", "Suspension", "", date(2026, 8, 3), 1000, "equal",
            [{"symbol": "000001.SZ"}, {"symbol": "600000.SH"}],
        )
        store.rebuild_custom_index("suspension")
        next_day = date(2026, 8, 6)
        store.upsert_trading_dates("tushare", [next_day])
        store.upsert_daily_bars("tushare", [
            DailyBar("000001.SZ", next_day, 12, 13, 11, 13, 130),
        ])
        store.upsert_adjustment_factors("tushare", [
            AdjustmentFactor("000001.SZ", next_day, 1),
        ])

        with pytest.raises(ValueError, match="missing constituent evidence: 600000.SH"):
            store.rebuild_custom_index("suspension", mode="incremental")

        store.upsert_stock_trade_statuses("tushare", [
            StockTradeStatus("600000.SH", next_day, "suspended"),
        ])
        rebuilt = store.rebuild_custom_index("suspension", mode="incremental")

        assert rebuilt["last_trade_date"] == next_day


def test_constituent_correction_marks_earliest_dependency_and_rebuilds_history():
    with _prepared_store() as store:
        store.create_custom_index(
            "correction", "Correction", "", date(2026, 8, 3), 1000, "equal",
            [{"symbol": "000001.SZ"}, {"symbol": "600000.SH"}],
        )
        store.rebuild_custom_index("correction")
        original = store.get_daily_bars("CINDEX:CORRECTION")

        store.upsert_daily_bars("tushare", [
            DailyBar("000001.SZ", date(2026, 8, 4), 10, 13, 9, 12, 110),
        ])
        assert store.has_dirty_custom_indices() is True

        store.rebuild_custom_index("correction", mode="incremental")
        corrected = store.get_daily_bars("CINDEX:CORRECTION")
        run = store._connection.execute(
            """
            SELECT mode, from_date, row_count FROM custom_index_calculation_runs
            WHERE index_id = 'correction' ORDER BY run_id DESC LIMIT 1
            """
        ).fetchone()

        assert corrected[1].close != original[1].close
        assert tuple(run) == ("correction", 20260804, 2)
        assert store.has_dirty_custom_indices() is False
