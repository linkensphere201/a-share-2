"""Minimal chart-serving data contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class InstrumentKind(StrEnum):
    STOCK = "stock"
    ETF = "etf"
    INDEX = "index"
    SECTOR = "sector"


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str
    name: str
    kind: InstrumentKind
    exchange: str
    active: bool = True


@dataclass(frozen=True, slots=True)
class DailyBar:
    symbol: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int

    def validate(self) -> None:
        if not self.symbol:
            raise ValueError("daily bar symbol is required")
        if self.volume < 0:
            raise ValueError("daily bar volume must be non-negative")
        if self.low > self.high:
            raise ValueError("daily bar low must not exceed high")
        if not self.low <= self.open <= self.high:
            raise ValueError("daily bar open must be inside low/high")
        if not self.low <= self.close <= self.high:
            raise ValueError("daily bar close must be inside low/high")


@dataclass(frozen=True, slots=True)
class StoredDailyBar:
    symbol: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    source: str
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class WriteStats:
    received: int
    changed: int
    unchanged: int
    elapsed_ms: float


@dataclass(frozen=True, slots=True)
class SymbolSyncState:
    source: str
    scope: str
    symbol: str
    covered_from: date
    covered_through: date
    last_batch_rows: int
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class ProviderBarRejection:
    symbol: str
    trade_date: date
    reason: str


@dataclass(frozen=True, slots=True)
class InstrumentCoverage:
    symbol: str
    name: str
    kind: InstrumentKind
    active: bool
    first_trade_date: date | None
    last_trade_date: date | None
    row_count: int
    source_rows: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    instrument: Instrument
    catalog_source: str
    source_system: str
    family: str
    category: str
    provider_symbol: str
    observed_on: date
    listed_on: date | None = None
    delisted_on: date | None = None
    constituent_count: int | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BoardMembership:
    board_symbol: str
    member_symbol: str
    member_name: str
    source: str
    observed_on: date


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    symbol: str
    trade_date: date
    change_percent: float
    total_market_cap: float | None = None


@dataclass(frozen=True, slots=True)
class EtfHolding:
    etf_symbol: str
    holding_symbol: str
    holding_name: str
    as_of_date: date
    quantity: float | None = None
    weight_percent: float | None = None
    market_value: float | None = None
    rank: int | None = None


@dataclass(frozen=True, slots=True)
class CoverageGap:
    source: str
    scope: str
    trade_date: date
    reason: str
    observed_at_ms: int


@dataclass(frozen=True, slots=True)
class ProviderIncident:
    incident_id: int
    source: str
    dataset: str
    scope: str
    trade_date: date
    incident_type: str
    status: str
    occurrence_count: int
    message: str
    first_observed_at_ms: int
    last_observed_at_ms: int
    resolved_at_ms: int | None
    resolution: str | None


@dataclass(frozen=True, slots=True)
class ValidationResult:
    primary_source: str
    validator_source: str
    symbol: str
    trade_date: date
    status: str
    message: str
    checked_at_ms: int


@dataclass(frozen=True, slots=True)
class RepairJob:
    job_id: int
    primary_source: str
    scope: str
    trade_date: date
    status: str
    attempt_count: int
    expected_rows: int
    repaired_rows: int
    unresolved_rows: int
    last_error: str | None
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class RepairBatch:
    instruments: tuple[Instrument, ...]
    bars: tuple[DailyBar, ...]
    failed_symbols: tuple[str, ...]
