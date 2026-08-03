from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from stock_harness.auto_update import IncrementalUpdater
from stock_harness.config import load_runtime_settings
from stock_harness.models import (
    CatalogEntry, DailyBar, EtfHolding, Instrument, InstrumentKind, MarketSnapshot,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore


TARGETS = {
    InstrumentKind.ETF: Instrument("510300.SH", "ETF", InstrumentKind.ETF, "SH"),
    InstrumentKind.INDEX: Instrument("000300.SH", "Index", InstrumentKind.INDEX, "SH"),
    InstrumentKind.SECTOR: Instrument("801010.SI", "Sector", InstrumentKind.SECTOR, "SI"),
}


class FakeProvider:
    code = "tushare"

    def __init__(self) -> None:
        self.rejected_bars = ()
        self.snapshot_fetches = 0

    def trading_dates(self, _start, _end):
        return [date(2026, 7, 31), date(2026, 8, 3)]

    def list_instruments(self):
        return [Instrument("600519.SH", "Stock", InstrumentKind.STOCK, "SH")]

    def list_all_equity_etfs(self, observed_on):
        return [self._entry(TARGETS[InstrumentKind.ETF], "tushare", "tushare", "etf", observed_on)]

    def list_broad_indices(self, _selections):
        return [TARGETS[InstrumentKind.INDEX]]

    def list_sectors(self, _source, _level):
        return [TARGETS[InstrumentKind.SECTOR]]

    def list_dc_boards(self, observed_on):
        item = Instrument("BK1128.DC", "DC", InstrumentKind.SECTOR, "DC")
        return [self._entry(item, "tushare_dc", "eastmoney", "dc", observed_on)]

    def list_ths_boards(self, observed_on):
        item = Instrument("886033.TI", "THS", InstrumentKind.SECTOR, "TI")
        return [self._entry(item, "tushare_ths", "ths", "ths", observed_on)]

    def fetch_daily_bars(self, trade_date):
        self.snapshot_fetches += 1
        self.rejected_bars = ()
        return [self._bar("600519.SH", trade_date)]

    def fetch_daily_snapshot(self, kind, trade_date):
        self.snapshot_fetches += 1
        self.rejected_bars = ()
        return [self._bar(TARGETS[kind].symbol, trade_date)]

    def fetch_board_daily_snapshot(self, source_system, trade_date):
        self.snapshot_fetches += 1
        self.rejected_bars = ()
        symbol = "BK1128.DC" if source_system == "eastmoney" else "886033.TI"
        return [self._bar(symbol, trade_date)]

    def fetch_stock_market_snapshots(self, trade_date):
        return [MarketSnapshot("600519.SH", trade_date, 10.0, 2_000_000_000)]

    def fetch_etf_holdings(self, symbol, candidate_dates):
        as_of = candidate_dates[0]
        return as_of, [EtfHolding(symbol, "600519.SH", "Stock", as_of, quantity=100, rank=1)]

    @staticmethod
    def _bar(symbol, trade_date):
        return DailyBar(symbol, trade_date, 10, 12, 9, 11, 100)

    @staticmethod
    def _entry(instrument, source, system, family, observed_on):
        return CatalogEntry(
            instrument, source, system, family, family, instrument.symbol, observed_on
        )


def test_incremental_update_persists_calendar_and_skips_completed_snapshots(tmp_path: Path):
    provider_config = tmp_path / "providers.yaml"
    storage_config = tmp_path / "storage.yaml"
    provider_config.write_text(
        """
providers:
  default: tushare
  tushare: {enabled: true, token_env: TEST_TOKEN, requests_per_minute: 0}
  auto_update: {enabled: true, poll_interval_seconds: 60, calendar_lookback_days: 14}
  universes:
    etfs: [{symbol: 510300.SH, category: broad, reason: test}]
    broad_indices: [{symbol: 000300.SH, category: broad, reason: test}]
    sector_source: SW2021
    sector_level: L1
  validation: {}
  repair: {}
""".strip(),
        encoding="utf-8",
    )
    storage_config.write_text(
        "storage: {database_path: market.sqlite, sqlite_mmap_size_mib: 0}",
        encoding="utf-8",
    )
    settings = load_runtime_settings(provider_config, storage_config)
    providers: list[FakeProvider] = []

    def factory():
        provider = FakeProvider()
        providers.append(provider)
        return provider

    updater = IncrementalUpdater(settings, factory)
    first = updater.run_once(datetime(2026, 8, 3, 19, 0))
    second = updater.run_once(datetime(2026, 8, 3, 19, 5))

    assert first.snapshots_checked == 12
    assert first.snapshots_written == 12
    assert first.rows_changed == 12
    assert first.errors == ()
    assert first.market_snapshot_rows == 7
    assert (first.etfs_checked, first.etfs_completed, first.holding_rows) == (1, 1, 1)
    assert providers[0].snapshot_fetches == 12
    assert second.snapshots_checked == 12
    assert second.snapshots_written == 0
    assert providers[1].snapshot_fetches == 0
    assert (second.etfs_checked, second.etfs_completed) == (0, 0)
    with SQLiteMarketDataStore(settings.database_path, mmap_size_mib=0) as store:
        assert store.list_trading_dates(
            "tushare", date(2026, 7, 1), date(2026, 8, 3)
        ) == [date(2026, 7, 31), date(2026, 8, 3)]
