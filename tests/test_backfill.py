from __future__ import annotations

import unittest
from datetime import date

from stock_harness.backfill import run_stock_backfill, years_ago
from stock_harness.models import DailyBar, Instrument, InstrumentKind
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


if __name__ == "__main__":
    unittest.main()
