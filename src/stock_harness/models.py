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
