from __future__ import annotations

import unittest
from datetime import date

from stock_harness.models import DailyBar, Instrument, InstrumentKind
from stock_harness.sqlite_store import SQLiteMarketDataStore


class SQLiteMarketDataStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = SQLiteMarketDataStore(":memory:")
        self.store.upsert_instruments(
            [Instrument("600519.SH", "Kweichow Moutai", InstrumentKind.STOCK, "SH")]
        )

    def tearDown(self) -> None:
        self.store.close()

    def test_insert_no_change_and_update_are_distinguished(self) -> None:
        original = DailyBar("600519.SH", date(2026, 7, 31), 1400.0, 1420.0, 1390.0, 1410.0, 123456)
        inserted = self.store.upsert_daily_bars("tushare", [original])
        unchanged = self.store.upsert_daily_bars("tushare", [original])
        corrected = self.store.upsert_daily_bars(
            "tushare",
            [DailyBar("600519.SH", date(2026, 7, 31), 1400.0, 1420.0, 1390.0, 1411.0, 123456)],
        )

        self.assertEqual((inserted.changed, inserted.unchanged), (1, 0))
        self.assertEqual((unchanged.changed, unchanged.unchanged), (0, 1))
        self.assertEqual((corrected.changed, corrected.unchanged), (1, 0))
        self.assertEqual(self.store.get_daily_bars("600519.SH")[0].close, 1411.0)

    def test_symbol_range_query_is_ordered_and_bounded(self) -> None:
        self.store.upsert_daily_bars(
            "tushare",
            [
                DailyBar("600519.SH", date(2026, 7, 30), 10, 12, 9, 11, 100),
                DailyBar("600519.SH", date(2026, 7, 31), 11, 13, 10, 12, 200),
                DailyBar("600519.SH", date(2026, 8, 1), 12, 14, 11, 13, 300),
            ],
        )

        rows = self.store.get_daily_bars(
            "600519.SH", start_date=date(2026, 7, 31), end_date=date(2026, 8, 1)
        )

        self.assertEqual([row.trade_date for row in rows], [date(2026, 7, 31), date(2026, 8, 1)])
        self.assertEqual([row.volume for row in rows], [200, 300])

    def test_unknown_instrument_fails_without_partial_write(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown instruments"):
            self.store.upsert_daily_bars(
                "tushare",
                [DailyBar("000001.SZ", date(2026, 7, 31), 10, 12, 9, 11, 100)],
            )
        self.assertEqual(self.store.count_daily_bars(), 0)

    def test_identical_complete_snapshot_uses_fast_no_change_path(self) -> None:
        bar = DailyBar("600519.SH", date(2026, 7, 31), 10, 12, 9, 11, 100)
        inserted = self.store.upsert_daily_snapshot("tushare", "stock", date(2026, 7, 31), [bar])
        unchanged = self.store.upsert_daily_snapshot("tushare", "stock", date(2026, 7, 31), [bar])

        self.assertEqual((inserted.changed, inserted.unchanged), (1, 0))
        self.assertEqual((unchanged.changed, unchanged.unchanged), (0, 1))

    def test_snapshot_dates_are_loaded_once_with_range_bounds(self) -> None:
        first_date = date(2026, 7, 30)
        second_date = date(2026, 7, 31)
        for trade_date in (first_date, second_date):
            self.store.upsert_daily_snapshot(
                "tushare",
                "stock",
                trade_date,
                [DailyBar("600519.SH", trade_date, 10, 12, 9, 11, 100)],
            )

        dates = self.store.list_daily_snapshot_dates(
            "tushare", "stock", second_date, second_date
        )

        self.assertEqual(dates, {second_date})

    def test_sqlite_memory_budget_is_bounded_and_configurable(self) -> None:
        with SQLiteMarketDataStore(
            ":memory:", cache_size_kib=2_048, mmap_size_mib=0, temp_store="FILE",
            busy_timeout_ms=1_234,
        ) as store:
            cache_size = store._connection.execute("PRAGMA cache_size").fetchone()[0]
            temp_store = store._connection.execute("PRAGMA temp_store").fetchone()[0]
            busy_timeout = store._connection.execute("PRAGMA busy_timeout").fetchone()[0]

        self.assertEqual(cache_size, -2_048)
        self.assertEqual(temp_store, 1)
        self.assertEqual(busy_timeout, 1_234)

    def test_passive_checkpoint_returns_sqlite_counters(self) -> None:
        busy, log_pages, checkpointed_pages = self.store.checkpoint("PASSIVE")

        self.assertEqual(busy, 0)
        self.assertGreaterEqual(log_pages, -1)
        self.assertGreaterEqual(checkpointed_pages, -1)

    def test_snapshot_rejects_mixed_dates(self) -> None:
        with self.assertRaisesRegex(ValueError, "share the requested trade date"):
            self.store.upsert_daily_snapshot(
                "tushare",
                "stock",
                date(2026, 7, 31),
                [DailyBar("600519.SH", date(2026, 7, 30), 10, 12, 9, 11, 100)],
            )

    def test_snapshot_rejects_duplicate_symbols(self) -> None:
        bar = DailyBar("600519.SH", date(2026, 7, 31), 10, 12, 9, 11, 100)
        with self.assertRaisesRegex(ValueError, "unique symbols"):
            self.store.upsert_daily_snapshot(
                "tushare", "stock", date(2026, 7, 31), [bar, bar]
            )

    def test_invalid_ohlc_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "open must be inside"):
            self.store.upsert_daily_bars(
                "tushare",
                [DailyBar("600519.SH", date(2026, 7, 31), 13, 12, 9, 11, 100)],
            )

    def test_fallback_source_cannot_overwrite_canonical_tushare_row(self) -> None:
        trade_date = date(2026, 7, 31)
        canonical = DailyBar("600519.SH", trade_date, 10, 12, 9, 11, 100)
        fallback = DailyBar("600519.SH", trade_date, 10, 12, 9, 10.5, 90)
        self.store.upsert_daily_bars("tushare", [canonical])

        stats = self.store.upsert_daily_bars("baostock", [fallback])
        stored = self.store.get_daily_bars("600519.SH")[0]

        self.assertEqual((stats.changed, stats.unchanged), (0, 1))
        self.assertEqual((stored.source, stored.close, stored.volume), ("tushare", 11, 100))


if __name__ == "__main__":
    unittest.main()
