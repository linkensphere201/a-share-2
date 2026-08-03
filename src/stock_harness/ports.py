"""Provider and storage boundaries for the minimal data layer."""

from __future__ import annotations

from datetime import date
from typing import Protocol, Sequence

from stock_harness.models import (
    BoardMembership,
    CatalogEntry,
    DailyBar,
    Instrument,
    EtfHolding,
    MarketSnapshot,
    RepairBatch,
    StoredDailyBar,
    SymbolSyncState,
    WriteStats,
)


class DailyMarketDataProvider(Protocol):
    @property
    def code(self) -> str: ...

    def list_instruments(self) -> Sequence[Instrument]: ...

    def fetch_daily_bars(self, trade_date: date) -> Sequence[DailyBar]: ...


class DailyBarValidationProvider(Protocol):
    @property
    def code(self) -> str: ...

    def fetch_symbol_daily_bars(
        self, symbol: str, start_date: date, end_date: date
    ) -> Sequence[DailyBar]: ...


class ConfiguredSymbolDailyProvider(Protocol):
    @property
    def code(self) -> str: ...

    def trading_dates(self, start_date: date, end_date: date) -> list[date]: ...

    def fetch_symbol_daily_bars(
        self, symbol: str, start_date: date, end_date: date
    ) -> Sequence[DailyBar]: ...


class TradeDateRepairProvider(Protocol):
    @property
    def code(self) -> str: ...

    def fetch_stock_trade_date(self, trade_date: date) -> RepairBatch: ...


class MarketDataStore(Protocol):
    def upsert_instruments(self, instruments: Sequence[Instrument]) -> int: ...

    def upsert_catalog_entries(self, entries: Sequence[CatalogEntry]) -> int: ...

    def replace_board_memberships(
        self,
        source: str,
        board_symbol: str,
        observed_on: date,
        memberships: Sequence[BoardMembership],
    ) -> int: ...

    def upsert_market_snapshots(
        self, source: str, snapshots: Sequence[MarketSnapshot]
    ) -> int: ...

    def replace_etf_holdings(
        self,
        source: str,
        etf_symbol: str,
        as_of_date: date,
        holdings: Sequence[EtfHolding],
    ) -> int: ...

    def upsert_daily_bars(self, source: str, bars: Sequence[DailyBar]) -> WriteStats: ...

    def upsert_daily_snapshot(
        self,
        source: str,
        scope: str,
        trade_date: date,
        bars: Sequence[DailyBar],
    ) -> WriteStats: ...

    def get_symbol_sync_state(
        self, source: str, scope: str, symbol: str
    ) -> SymbolSyncState | None: ...

    def upsert_symbol_history(
        self,
        source: str,
        scope: str,
        symbol: str,
        covered_from: date,
        covered_through: date,
        bars: Sequence[DailyBar],
    ) -> WriteStats: ...

    def get_daily_bars(
        self,
        symbol: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[StoredDailyBar]: ...
