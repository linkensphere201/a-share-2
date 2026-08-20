"""Minimal chart-serving data contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from math import isfinite


class InstrumentKind(StrEnum):
    STOCK = "stock"
    ETF = "etf"
    INDEX = "index"
    SECTOR = "sector"
    CUSTOM_INDEX = "custom-index"
    FUTURES_PRODUCT = "futures-product"
    FUTURES_CONTRACT = "futures-contract"
    FUTURES_CONTINUOUS = "futures-continuous"


class FuturesExchange(StrEnum):
    CFFEX = "CFFEX"
    DCE = "DCE"
    CZCE = "CZCE"
    SHFE = "SHFE"
    INE = "INE"
    GFEX = "GFEX"


class FuturesLifecycleStatus(StrEnum):
    PENDING = "pending"
    LISTED = "listed"
    TRADING = "trading"
    EXPIRED = "expired"
    DELIVERED = "delivered"
    DELISTED = "delisted"


class FuturesSeriesKind(StrEnum):
    MAIN = "main"
    CONTINUOUS = "continuous"


class FuturesPriceBasis(StrEnum):
    RAW = "raw"
    BACKWARD_RATIO = "backward-ratio"
    BACKWARD_ADDITIVE = "backward-additive"


class FuturesBarState(StrEnum):
    FINAL = "final"
    PROVISIONAL = "provisional"


class FuturesSessionPhase(StrEnum):
    DAY = "day"
    NIGHT = "night"


class FuturesChangeBasis(StrEnum):
    PREVIOUS_SETTLEMENT = "previous-settlement"
    PREVIOUS_CLOSE = "previous-close"


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str
    name: str
    kind: InstrumentKind
    exchange: str
    active: bool = True


@dataclass(frozen=True, slots=True)
class FuturesProduct:
    symbol: str
    product_code: str
    display_name: str
    exchange: FuturesExchange
    multiplier: float
    trading_unit: str
    quote_unit: str
    active: bool = True

    def validate(self) -> None:
        _require_symbol(self.symbol, "futures product")
        _require_text(self.product_code, "futures product code")
        _require_text(self.display_name, "futures product display name")
        _require_positive(self.multiplier, "futures product multiplier")
        _require_text(self.trading_unit, "futures product trading unit")
        _require_text(self.quote_unit, "futures product quote unit")


@dataclass(frozen=True, slots=True)
class FuturesContract:
    symbol: str
    provider_symbol: str
    product_symbol: str
    display_name: str
    exchange: FuturesExchange
    contract_month: str
    listed_on: date
    last_trading_date: date
    delivery_date: date | None
    multiplier: float
    trading_unit: str
    quote_unit: str
    lifecycle_status: FuturesLifecycleStatus

    def validate(self) -> None:
        _require_symbol(self.symbol, "futures contract")
        _require_text(self.provider_symbol, "futures provider symbol")
        _require_symbol(self.product_symbol, "futures product")
        _require_text(self.display_name, "futures contract display name")
        _require_text(self.contract_month, "futures contract month")
        if self.last_trading_date < self.listed_on:
            raise ValueError("futures last trading date must not precede listing date")
        if self.delivery_date is not None and self.delivery_date < self.last_trading_date:
            raise ValueError("futures delivery date must not precede last trading date")
        _require_positive(self.multiplier, "futures contract multiplier")
        _require_text(self.trading_unit, "futures contract trading unit")
        _require_text(self.quote_unit, "futures contract quote unit")


@dataclass(frozen=True, slots=True)
class FuturesContinuousSeries:
    symbol: str
    product_symbol: str
    display_name: str
    exchange: FuturesExchange
    series_kind: FuturesSeriesKind
    price_basis: FuturesPriceBasis
    rule_version: str
    active: bool = True

    def validate(self) -> None:
        _require_symbol(self.symbol, "futures continuous series")
        _require_symbol(self.product_symbol, "futures product")
        _require_text(self.display_name, "futures continuous display name")
        _require_text(self.rule_version, "futures continuous rule version")


@dataclass(frozen=True, slots=True)
class FuturesTradingDayOwnership:
    trading_day: date
    calendar_date: date
    provider_date: date
    exchange: FuturesExchange
    session_phase: FuturesSessionPhase

    def validate(self) -> None:
        if self.session_phase is FuturesSessionPhase.DAY and self.calendar_date != self.trading_day:
            raise ValueError("futures day session calendar date must equal trading day")
        if self.session_phase is FuturesSessionPhase.NIGHT and self.calendar_date >= self.trading_day:
            raise ValueError("futures night session calendar date must precede trading day")


@dataclass(frozen=True, slots=True)
class FuturesDailyBar:
    symbol: str
    trading_day: date
    provider_date: date
    open: float
    high: float
    low: float
    close: float
    previous_close: float | None
    settlement: float | None
    previous_settlement: float | None
    volume_contracts: int
    amount: float | None
    open_interest_contracts: float | None
    open_interest_change_contracts: float | None
    delivery_settlement: float | None
    source: str
    state: FuturesBarState
    provider_time: datetime | None = None
    mapped_contract_symbol: str | None = None
    roll_event: bool = False

    def validate(self) -> None:
        _require_symbol(self.symbol, "futures daily bar")
        _validate_ohlc(self.open, self.high, self.low, self.close, "futures daily bar")
        if self.volume_contracts < 0:
            raise ValueError("futures daily volume must be non-negative contracts")
        for name, value in (
            ("previous close", self.previous_close),
            ("settlement", self.settlement),
            ("previous settlement", self.previous_settlement),
            ("amount", self.amount),
            ("open interest", self.open_interest_contracts),
            ("delivery settlement", self.delivery_settlement),
        ):
            if value is not None and (not isfinite(value) or value < 0):
                raise ValueError(f"futures daily {name} must be finite and non-negative")
        if self.open_interest_change_contracts is not None and not isfinite(
            self.open_interest_change_contracts
        ):
            raise ValueError("futures daily open-interest change must be finite")
        _require_text(self.source, "futures daily source")
        if self.state is FuturesBarState.PROVISIONAL and self.provider_time is None:
            raise ValueError("provisional futures daily bar requires provider time")

    @property
    def candle_direction(self) -> int:
        return (self.close > self.open) - (self.close < self.open)

    def change_percent(
        self,
        basis: FuturesChangeBasis = FuturesChangeBasis.PREVIOUS_SETTLEMENT,
    ) -> float | None:
        previous = (
            self.previous_settlement
            if basis is FuturesChangeBasis.PREVIOUS_SETTLEMENT
            else self.previous_close
        )
        if previous is None or previous == 0:
            return None
        return (self.close / previous - 1.0) * 100.0

    def to_chart_bar(self) -> DailyBar:
        return DailyBar(
            symbol=self.symbol,
            trade_date=self.trading_day,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume_contracts,
        )


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
    close: float | None = None
    volume: int | None = None
    amount: float | None = None


@dataclass(frozen=True, slots=True)
class ProvisionalDailyBar:
    symbol: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float
    previous_close: float
    change_percent: float
    source: str
    provider_time: datetime
    received_at: datetime


@dataclass(frozen=True, slots=True)
class AdjustmentFactor:
    symbol: str
    trade_date: date
    factor: float

    def validate(self) -> None:
        if not self.symbol:
            raise ValueError("adjustment factor symbol is required")
        if self.factor <= 0:
            raise ValueError("adjustment factor must be positive")


@dataclass(frozen=True, slots=True)
class StockTradeStatus:
    symbol: str
    trade_date: date
    status: str

    def validate(self) -> None:
        if not self.symbol:
            raise ValueError("stock trade status symbol is required")
        if self.status not in {"listed", "trading", "suspended", "delisted"}:
            raise ValueError(f"invalid stock trade status: {self.status}")


@dataclass(frozen=True, slots=True)
class CustomIndexMember:
    symbol: str
    raw_weight: float
    normalized_weight: float


@dataclass(frozen=True, slots=True)
class CustomIndexBar:
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    daily_return: float
    eligible_count: int
    total_count: int
    quality_status: str
    input_hash: bytes


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


def canonical_futures_product_symbol(
    exchange: FuturesExchange,
    product_code: str,
) -> str:
    return f"FUTPROD:{exchange.value}:{_canonical_token(product_code, 'product code')}"


def canonical_futures_contract_symbol(
    exchange: FuturesExchange,
    product_code: str,
    contract_month: str,
) -> str:
    product = _canonical_token(product_code, "product code")
    month = contract_month.strip()
    if len(month) != 6 or not month.isdigit() or not 1 <= int(month[-2:]) <= 12:
        raise ValueError("futures contract month must use YYYYMM")
    return f"FUT:{exchange.value}:{product}:{month}"


def canonical_futures_continuous_symbol(
    exchange: FuturesExchange,
    product_code: str,
    series_kind: FuturesSeriesKind,
    price_basis: FuturesPriceBasis,
) -> str:
    product = _canonical_token(product_code, "product code")
    return f"FUTCONT:{exchange.value}:{product}:{series_kind.value}:{price_basis.value}"


def _canonical_token(value: str, label: str) -> str:
    token = value.strip().upper()
    if not token or not token.isalnum():
        raise ValueError(f"futures {label} must contain only letters and digits")
    return token


def _require_symbol(value: str, label: str) -> None:
    _require_text(value, f"{label} symbol")


def _require_text(value: str, label: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{label} is required")


def _require_positive(value: float, label: str) -> None:
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be finite and positive")


def _validate_ohlc(
    open_price: float,
    high: float,
    low: float,
    close: float,
    label: str,
) -> None:
    if not all(isfinite(value) for value in (open_price, high, low, close)):
        raise ValueError(f"{label} OHLC must be finite")
    if low > high:
        raise ValueError(f"{label} low must not exceed high")
    if not low <= open_price <= high:
        raise ValueError(f"{label} open must be inside low/high")
    if not low <= close <= high:
        raise ValueError(f"{label} close must be inside low/high")
