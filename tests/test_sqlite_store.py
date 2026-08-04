from __future__ import annotations

import unittest
from datetime import date

from stock_harness.models import (
    BoardMembership,
    CatalogEntry,
    DailyBar,
    EtfHolding,
    Instrument,
    InstrumentKind,
    MarketSnapshot,
)
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

    def test_symbol_history_commits_bars_and_coverage_state_together(self) -> None:
        start = date(2026, 7, 1)
        end = date(2026, 7, 31)
        bar = DailyBar("600519.SH", end, 10, 12, 9, 11, 100)

        stats = self.store.upsert_symbol_history(
            "tushare", "test", "600519.SH", start, end, [bar]
        )
        state = self.store.get_symbol_sync_state("tushare", "test", "600519.SH")

        self.assertEqual(stats.changed, 1)
        self.assertIsNotNone(state)
        self.assertEqual((state.covered_from, state.covered_through), (start, end))
        self.assertEqual(state.last_batch_rows, 1)

    def test_empty_symbol_increment_advances_coverage_without_bars(self) -> None:
        first_end = date(2026, 7, 31)
        second_end = date(2026, 8, 2)
        self.store.upsert_symbol_history(
            "tushare", "test", "600519.SH", date(2026, 7, 1), first_end, []
        )

        stats = self.store.upsert_symbol_history(
            "tushare", "test", "600519.SH", date(2026, 8, 1), second_end, []
        )
        state = self.store.get_symbol_sync_state("tushare", "test", "600519.SH")

        self.assertEqual(stats.received, 0)
        self.assertEqual(state.covered_through, second_end)

    def test_instrument_coverage_reports_dates_rows_and_sources(self) -> None:
        trade_date = date(2026, 7, 31)
        self.store.upsert_daily_bars(
            "tushare", [DailyBar("600519.SH", trade_date, 10, 12, 9, 11, 100)]
        )

        coverage = self.store.list_instrument_coverage({InstrumentKind.STOCK})[0]

        self.assertEqual((coverage.first_trade_date, coverage.last_trade_date), (trade_date, trade_date))
        self.assertEqual(coverage.row_count, 1)
        self.assertEqual(coverage.source_rows, (("tushare", 1),))

    def test_catalog_and_memberships_preserve_provider_identity(self) -> None:
        observed_on = date(2026, 7, 31)
        board = Instrument("BK1128.DC", "CPO", InstrumentKind.SECTOR, "DC")
        entry = CatalogEntry(
            board, "tushare_dc", "eastmoney", "eastmoney_board",
            "concept", board.symbol, observed_on, aliases=("CPO concept",),
        )

        self.store.upsert_catalog_entries([entry])
        self.store.replace_board_memberships(
            "tushare_dc",
            board.symbol,
            observed_on,
            [BoardMembership(board.symbol, "300308.SZ", "Innolight", "tushare_dc", observed_on)],
        )

        catalog = self.store._connection.execute(
            """
            SELECT profile.acquired_via, profile.source_system, catalog.family,
                   catalog.category, instrument.symbol
            FROM instrument_catalog_entries AS catalog
            JOIN sources AS source ON source.source_id = catalog.catalog_source_id
            JOIN source_profiles AS profile USING (source_id)
            JOIN instruments AS instrument USING (instrument_id)
            """
        ).fetchone()
        membership = self.store._connection.execute(
            "SELECT member_symbol, active FROM board_memberships"
        ).fetchone()

        self.assertEqual(tuple(catalog), ("tushare", "eastmoney", "eastmoney_board", "concept", "BK1128.DC"))
        self.assertEqual(tuple(membership), ("300308.SZ", 1))

    def test_instrument_search_matches_full_and_initial_pinyin(self) -> None:
        instrument = Instrument("600519.SH", "贵州茅台", InstrumentKind.STOCK, "SH")
        self.store.upsert_instruments([instrument])

        initials = self.store.search_instruments("GZMT")
        full = self.store.search_instruments("guizhoumaotai")

        self.assertEqual([item["symbol"] for item in initials], [instrument.symbol])
        self.assertEqual([item["symbol"] for item in full], [instrument.symbol])

    def test_instrument_name_change_replaces_pinyin_aliases(self) -> None:
        original = Instrument("600519.SH", "贵州茅台", InstrumentKind.STOCK, "SH")
        renamed = Instrument("600519.SH", "茅台集团", InstrumentKind.STOCK, "SH")
        self.store.upsert_instruments([original])
        self.store.upsert_instruments([renamed])

        self.assertEqual(self.store.search_instruments("gzmt"), [])
        self.assertEqual(self.store.search_instruments("mtjt")[0]["symbol"], original.symbol)

    def test_market_snapshots_are_derived_and_stock_cap_is_enriched(self) -> None:
        self.store.upsert_daily_bars("tushare", [
            DailyBar("600519.SH", date(2026, 7, 31), 10, 10, 10, 10, 100),
            DailyBar("600519.SH", date(2026, 8, 3), 11, 11, 11, 11, 100),
        ])
        self.store.derive_market_snapshots(date(2026, 8, 3))
        self.store.upsert_market_snapshots("tushare", [
            MarketSnapshot("600519.SH", date(2026, 8, 3), 10.0, 2_000_000_000),
        ])

        snapshot = self.store.list_market_snapshots(["600519.SH"])[0]

        self.assertEqual(snapshot["trade_date"], date(2026, 8, 3))
        self.assertAlmostEqual(snapshot["change_percent"], 10.0)
        self.assertEqual(snapshot["total_market_cap"], 2_000_000_000)

    def test_etf_holdings_keep_as_of_date_and_receipt(self) -> None:
        etf = Instrument("510300.SH", "ETF", InstrumentKind.ETF, "SH")
        self.store.upsert_instruments([etf])
        as_of = date(2026, 8, 3)
        holdings = [EtfHolding(etf.symbol, "600519.SH", "Moutai", as_of, 100, rank=1)]

        self.store.replace_etf_holdings("tushare_etf_pcf", etf.symbol, as_of, holdings)
        self.store.record_etf_holding_receipt(
            "tushare_etf_pcf", etf.symbol, as_of, as_of, 1
        )
        result = self.store.list_etf_holdings(etf.symbol)

        self.assertEqual(result["as_of_date"], as_of)
        self.assertEqual(result["items"][0]["symbol"], "600519.SH")
        self.assertEqual(
            self.store.list_etfs_needing_holding_refresh(
                "tushare_etf_pcf", as_of, 10
            ),
            [],
        )

    def test_custom_groups_persist_order_tags_and_updates(self) -> None:
        created = self.store.create_custom_group(
            "group-one", "Core Tech", "manual collection", [
                {"symbol": "600519.SH", "tags": ["leader"], "note": "watch"},
            ],
        )
        updated = self.store.update_custom_group(
            "group-one", "Core Tech 2", "updated", [
                {"symbol": "600519.SH", "tags": ["long-term"], "note": "hold"},
            ],
        )

        self.assertEqual(created["members"][0]["tags"], ["leader"])
        self.assertEqual(updated["name"], "Core Tech 2")
        self.assertEqual(updated["members"][0]["note"], "hold")
        self.assertEqual(self.store.list_custom_groups("Tech")[0]["member_count"], 1)
        self.assertTrue(self.store.delete_custom_group("group-one"))
        self.assertIsNone(self.store.get_custom_group("group-one"))

    def test_volume_scale_migration_is_idempotent(self) -> None:
        trade_date = date(2026, 7, 31)
        self.store.upsert_daily_bars(
            "tushare_ths",
            [DailyBar("600519.SH", trade_date, 10, 12, 9, 11, 123)],
        )

        first = self.store.apply_volume_scale_migration(
            "2026-08-02-ths-volume-shares", "tushare_ths", 100
        )
        second = self.store.apply_volume_scale_migration(
            "2026-08-02-ths-volume-shares", "tushare_ths", 100
        )

        self.assertEqual((first, second), (1, 1))
        self.assertEqual(self.store.get_daily_bars("600519.SH")[0].volume, 12_300)

    def test_catalog_exclusion_migration_preserves_history(self) -> None:
        instrument = Instrument(
            "501011.SH", "ETF\u8054\u63a5(LOF)", InstrumentKind.ETF, "SH"
        )
        self.store.upsert_catalog_entries([
            CatalogEntry(
                instrument, "tushare", "tushare", "exchange_traded_equity_fund",
                "stock", instrument.symbol, date(2026, 8, 2),
            )
        ])
        self.store.upsert_daily_bars(
            "tushare", [DailyBar(instrument.symbol, date(2026, 7, 31), 10, 12, 9, 11, 100)]
        )

        affected = self.store.apply_catalog_name_exclusion_migration(
            "exclude-links", "tushare", "exchange_traded_equity_fund", "\u8054\u63a5"
        )

        self.assertEqual(affected, 1)
        self.assertEqual(self.store.list_catalog_coverage_rows(), [])
        self.assertEqual(len(self.store.get_daily_bars(instrument.symbol)), 1)


if __name__ == "__main__":
    unittest.main()
