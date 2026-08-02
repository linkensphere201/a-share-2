from __future__ import annotations

import unittest
from datetime import date

from stock_harness.backfill import run_stock_backfill, run_symbol_backfill, years_ago
from stock_harness.models import DailyBar, Instrument, InstrumentKind, ProviderBarRejection
from stock_harness.sqlite_store import SQLiteMarketDataStore


class _Provider:
    code = "fake"

    def __init__(self):
        self.fetched_dates: list[date] = []

    def list_instruments(self):
        return [Instrument("600519.SH", "Moutai", InstrumentKind.STOCK, "SH")]

    def trading_dates(self, _start_date: date, _end_date: date):
        return [date(2026, 7, 30), date(2026, 7, 31)]

    def fetch_daily_bars(self, trade_date: date):
        self.fetched_dates.append(trade_date)
        return [DailyBar("600519.SH", trade_date, 10, 12, 9, 11, 100)]


class _ProviderWithHistoricalGap(_Provider):
    def fetch_daily_bars(self, trade_date: date):
        return [
            DailyBar("600519.SH", trade_date, 10, 12, 9, 11, 100),
            DailyBar("000022.SZ", trade_date, 5, 6, 4, 5, 50),
        ]


class _ProviderWithEmptyDate(_Provider):
    def fetch_daily_bars(self, trade_date: date):
        if trade_date == date(2026, 7, 30):
            return []
        return super().fetch_daily_bars(trade_date)


class _SymbolProvider(_Provider):
    def __init__(self):
        super().__init__()
        self.fetched_ranges: list[tuple[str, date, date]] = []
        self.rejected_bars = ()

    def fetch_symbol_daily_bars(self, kind, symbol, start_date, end_date):
        del kind
        self.fetched_ranges.append((symbol, start_date, end_date))
        return [DailyBar(symbol, end_date, 10, 12, 9, 11, 100)]


class _RejectingSymbolProvider(_SymbolProvider):
    def fetch_symbol_daily_bars(self, kind, symbol, start_date, end_date):
        bars = super().fetch_symbol_daily_bars(kind, symbol, start_date, end_date)
        self.rejected_bars = (
            ProviderBarRejection(symbol, date(2026, 7, 30), "close outside low/high"),
        )
        return bars


class _SectorFallback:
    code = "akshare_sw"

    def fetch_symbol_daily_bars(self, symbol, start_date, end_date):
        assert (start_date, end_date) == (date(2026, 7, 30), date(2026, 7, 30))
        return [DailyBar(symbol, start_date, 10, 12, 9, 11, 123)]


class BackfillTests(unittest.TestCase):
    def test_backfill_resumes_from_snapshot_receipts(self) -> None:
        with SQLiteMarketDataStore(":memory:") as store:
            first_provider = _Provider()
            second_provider = _Provider()
            first = run_stock_backfill(
                first_provider, store, date(2026, 7, 1), date(2026, 7, 31)
            )
            second = run_stock_backfill(
                second_provider, store, date(2026, 7, 1), date(2026, 7, 31)
            )

            self.assertEqual((first.completed_dates, first.rows_written), (2, 2))
            self.assertEqual((second.skipped_dates, second.completed_dates), (2, 0))
            self.assertEqual(first_provider.fetched_dates, first_provider.trading_dates(None, None))
            self.assertEqual(second_provider.fetched_dates, [])
            self.assertEqual(store.count_daily_bars(), 2)

    def test_stock_refresh_rechecks_only_requested_recent_trading_dates(self) -> None:
        with SQLiteMarketDataStore(":memory:") as store:
            run_stock_backfill(_Provider(), store, date(2026, 7, 1), date(2026, 7, 31))
            refresh_provider = _Provider()
            result = run_stock_backfill(
                refresh_provider,
                store,
                date(2026, 7, 1),
                date(2026, 7, 31),
                refresh_last_trading_days=1,
            )

        self.assertEqual(refresh_provider.fetched_dates, [date(2026, 7, 31)])
        self.assertEqual((result.skipped_dates, result.completed_dates, result.rows_written), (1, 1, 0))

    def test_backfill_registers_historical_symbols_missing_from_master(self) -> None:
        with SQLiteMarketDataStore(":memory:") as store:
            result = run_stock_backfill(
                _ProviderWithHistoricalGap(), store, date(2026, 7, 1), date(2026, 7, 31), max_dates=1
            )

            self.assertEqual(result.discovered_instruments, 1)
            self.assertEqual(store.count_daily_bars(), 2)
            self.assertEqual(len(store.get_daily_bars("000022.SZ")), 1)

    def test_years_ago_handles_leap_day(self) -> None:
        self.assertEqual(years_ago(date(2024, 2, 29), 1), date(2023, 2, 28))

    def test_empty_open_date_is_recorded_without_stopping_backfill(self) -> None:
        with SQLiteMarketDataStore(":memory:") as store:
            result = run_stock_backfill(
                _ProviderWithEmptyDate(), store, date(2026, 7, 1), date(2026, 7, 31)
            )

            self.assertEqual((result.completed_dates, result.empty_dates), (1, 1))
            gaps = store.list_coverage_gaps("fake", "stock")
            self.assertEqual([gap.trade_date for gap in gaps], [date(2026, 7, 30)])
            incidents = store.list_provider_incidents()
            self.assertEqual(incidents[0].incident_type, "empty_open_trade_date")
            self.assertEqual(incidents[0].status, "open")
            self.assertEqual(store.count_daily_bars(), 1)

    def test_symbol_backfill_resumes_by_symbol_coverage_state(self) -> None:
        instrument = Instrument("510300.SH", "CSI 300 ETF", InstrumentKind.ETF, "SH")
        with SQLiteMarketDataStore(":memory:") as store:
            first_provider = _SymbolProvider()
            first = run_symbol_backfill(
                first_provider, store, "etf", InstrumentKind.ETF, [instrument],
                date(2026, 7, 1), date(2026, 7, 31),
            )
            second_provider = _SymbolProvider()
            second = run_symbol_backfill(
                second_provider, store, "etf", InstrumentKind.ETF, [instrument],
                date(2026, 7, 1), date(2026, 7, 31),
            )
            third_provider = _SymbolProvider()
            third = run_symbol_backfill(
                third_provider, store, "etf", InstrumentKind.ETF, [instrument],
                date(2026, 7, 1), date(2026, 8, 3),
            )

        self.assertEqual((first.completed_symbols, first.changed_rows), (1, 1))
        self.assertEqual((second.skipped_symbols, second.completed_symbols), (1, 0))
        self.assertEqual(
            third_provider.fetched_ranges,
            [("510300.SH", date(2026, 8, 1), date(2026, 8, 3))],
        )
        self.assertEqual(third.changed_rows, 1)

    def test_symbol_refresh_rechecks_an_explicit_overlap(self) -> None:
        instrument = Instrument("510300.SH", "CSI 300 ETF", InstrumentKind.ETF, "SH")
        with SQLiteMarketDataStore(":memory:") as store:
            run_symbol_backfill(
                _SymbolProvider(), store, "etf", InstrumentKind.ETF, [instrument],
                date(2026, 7, 1), date(2026, 7, 31),
            )
            refresh_provider = _SymbolProvider()
            result = run_symbol_backfill(
                refresh_provider, store, "etf", InstrumentKind.ETF, [instrument],
                date(2026, 7, 1), date(2026, 7, 31),
                force_refresh_from=date(2026, 7, 25),
            )

        self.assertEqual(
            refresh_provider.fetched_ranges,
            [(instrument.symbol, date(2026, 7, 25), date(2026, 7, 31))],
        )
        self.assertEqual((result.completed_symbols, result.changed_rows), (1, 0))

    def test_symbol_backfill_can_extend_an_existing_cursor_backwards(self) -> None:
        instrument = Instrument("510300.SH", "CSI 300 ETF", InstrumentKind.ETF, "SH")
        with SQLiteMarketDataStore(":memory:") as store:
            first_provider = _SymbolProvider()
            run_symbol_backfill(
                first_provider, store, "etf", InstrumentKind.ETF, [instrument],
                date(2026, 7, 1), date(2026, 7, 31),
            )
            historical_provider = _SymbolProvider()
            run_symbol_backfill(
                historical_provider, store, "etf", InstrumentKind.ETF, [instrument],
                date(1996, 8, 2), date(2026, 7, 31),
            )
            state = store.get_symbol_sync_state("fake", "etf", instrument.symbol)

        self.assertEqual(
            historical_provider.fetched_ranges,
            [(instrument.symbol, date(1996, 8, 2), date(2026, 6, 30))],
        )
        self.assertEqual((state.covered_from, state.covered_through), (date(1996, 8, 2), date(2026, 7, 31)))

    def test_symbol_backfill_repairs_rejected_bar_before_advancing_cursor(self) -> None:
        instrument = Instrument("801010.SI", "Agriculture", InstrumentKind.SECTOR, "SI")
        with SQLiteMarketDataStore(":memory:") as store:
            result = run_symbol_backfill(
                _RejectingSymbolProvider(),
                store,
                "sector",
                InstrumentKind.SECTOR,
                [instrument],
                date(2026, 7, 1),
                date(2026, 7, 31),
                fallback_provider=_SectorFallback(),
            )
            bars = store.get_daily_bars("801010.SI")
            incidents = store.list_provider_incidents()
            state = store.get_symbol_sync_state("fake", "sector", "801010.SI")

        self.assertEqual(result.completed_symbols, 1)
        self.assertEqual([(bar.trade_date, bar.source) for bar in bars], [
            (date(2026, 7, 30), "akshare_sw"),
            (date(2026, 7, 31), "fake"),
        ])
        self.assertEqual(incidents[0].status, "resolved")
        self.assertEqual(state.covered_through, date(2026, 7, 31))

    def test_symbol_backfill_can_retain_audited_rejection_without_fabrication(self) -> None:
        instrument = Instrument("991001.TI", "Legacy Index", InstrumentKind.SECTOR, "TI")
        with SQLiteMarketDataStore(":memory:") as store:
            result = run_symbol_backfill(
                _RejectingSymbolProvider(),
                store,
                "ths_board",
                InstrumentKind.SECTOR,
                [instrument],
                date(2026, 7, 1),
                date(2026, 7, 31),
                allow_unrepaired_rejections=True,
            )
            incidents = store.list_provider_incidents()
            state = store.get_symbol_sync_state("fake", "ths_board", instrument.symbol)

        self.assertEqual(result.completed_symbols, 1)
        self.assertEqual(incidents[0].status, "open")
        self.assertEqual(incidents[0].incident_type, "invalid_daily_bar")
        self.assertEqual(state.covered_through, date(2026, 7, 31))


if __name__ == "__main__":
    unittest.main()
