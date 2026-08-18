"""Causal, chart-independent inputs for registered trading systems."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from typing import Protocol, Sequence

from stock_harness.models import (
    AdjustmentFactor,
    InstrumentKind,
    ProvisionalDailyBar,
    StockTradeStatus,
    StoredDailyBar,
)


class AnalysisTimeframe(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class AnalysisInputMode(StrEnum):
    FINAL = "final"
    PREVIEW = "preview"


@dataclass(frozen=True, slots=True)
class AnalysisHorizons:
    short: int = 60
    medium: int = 120
    long: int = 250

    def validate(self) -> None:
        if not 1 <= self.short <= self.medium <= self.long <= 1250:
            raise ValueError("analysis horizons must satisfy 1 <= short <= medium <= long <= 1250")


@dataclass(frozen=True, slots=True)
class AnalysisHorizonRange:
    name: str
    requested_bars: int
    available_bars: int
    start_date: date | None
    end_date: date | None


@dataclass(frozen=True, slots=True)
class AnalysisInputWarning:
    code: str
    severity: str
    message: str
    dates: tuple[date, ...] = ()
    count: int = 0


@dataclass(frozen=True, slots=True)
class AnalysisBar:
    period_start: date
    period_end: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    sources: tuple[str, ...]
    contains_provisional: bool
    period_complete: bool
    observed_at_ms: int


@dataclass(frozen=True, slots=True)
class AnalysisInput:
    symbol: str
    timeframe: AnalysisTimeframe
    mode: AnalysisInputMode
    as_of_date: date
    price_basis: str
    volume_semantics: str
    missing_bar_policy: str
    horizons: AnalysisHorizons
    horizon_ranges: tuple[AnalysisHorizonRange, ...]
    warnings: tuple[AnalysisInputWarning, ...]
    bars: tuple[AnalysisBar, ...]
    latest_final_date: date | None
    provisional_date: date | None
    provisional_source: str | None
    provisional_provider_time: datetime | None


class AnalysisInputStore(Protocol):
    def get_recent_daily_bars(
        self, symbol: str, end_date: date, limit: int
    ) -> list[StoredDailyBar]: ...

    def get_instrument_kind(self, symbol: str) -> InstrumentKind | None: ...

    def get_instrument_lifecycle(
        self, symbol: str
    ) -> tuple[date | None, date | None]: ...

    def get_adjustment_factors(
        self, symbol: str, start_date: date, end_date: date
    ) -> list[AdjustmentFactor]: ...

    def get_stock_trade_statuses(
        self, symbol: str, start_date: date, end_date: date
    ) -> list[StockTradeStatus]: ...

    def list_trading_dates(
        self, source: str, start_date: date, end_date: date
    ) -> list[date]: ...

    def get_latest_provisional_daily_bar(
        self, symbol: str
    ) -> ProvisionalDailyBar | None: ...


@dataclass(frozen=True, slots=True)
class _SourceBar:
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    source: str
    provisional: bool
    observed_at_ms: int


class AnalysisInputService:
    """Build inputs using only evidence available through one explicit date."""

    def __init__(self, store: AnalysisInputStore, calendar_source: str = "tushare") -> None:
        self._store = store
        self._calendar_source = calendar_source

    def build(
        self,
        symbol: str,
        as_of_date: date,
        timeframe: AnalysisTimeframe = AnalysisTimeframe.DAILY,
        mode: AnalysisInputMode = AnalysisInputMode.FINAL,
        horizons: AnalysisHorizons = AnalysisHorizons(),
    ) -> AnalysisInput:
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol:
            raise ValueError("analysis symbol is required")

        horizons.validate()
        final_rows = self._store.get_recent_daily_bars(
            normalized_symbol, as_of_date, _daily_read_limit(timeframe, horizons.long)
        )
        final_bars = sorted(
            (_stored_bar(row) for row in final_rows if row.trade_date <= as_of_date),
            key=lambda row: row.trade_date,
        )
        latest_final_date = final_bars[-1].trade_date if final_bars else None

        provisional = None
        if mode is AnalysisInputMode.PREVIEW:
            candidate = self._store.get_latest_provisional_daily_bar(normalized_symbol)
            if (
                candidate is not None
                and candidate.trade_date <= as_of_date
                and (latest_final_date is None or candidate.trade_date > latest_final_date)
            ):
                provisional = candidate

        source_bars = final_bars
        if provisional is not None:
            source_bars = [*source_bars, _provisional_bar(provisional)]

        kind = self._store.get_instrument_kind(normalized_symbol)
        warnings: list[AnalysisInputWarning] = []
        price_basis = "raw"
        if kind is InstrumentKind.STOCK and source_bars:
            source_bars, price_basis, adjustment_warning = self._adjust_stock_prices(
                normalized_symbol, source_bars, as_of_date
            )
            if adjustment_warning is not None:
                warnings.append(adjustment_warning)

        trading_dates: list[date] = []
        if source_bars:
            trading_dates = self._store.list_trading_dates(
                self._calendar_source,
                source_bars[0].trade_date,
                as_of_date + timedelta(days=40),
            )
            missing_warning = self._missing_bar_warning(
                normalized_symbol, kind, source_bars, trading_dates, as_of_date
            )
            if missing_warning is not None:
                warnings.append(missing_warning)

        volume_semantics, volume_warning = _volume_capability(kind)
        if volume_warning is not None:
            warnings.append(volume_warning)

        bars = tuple(_aggregate(source_bars, timeframe, trading_dates))

        return AnalysisInput(
            symbol=normalized_symbol,
            timeframe=timeframe,
            mode=mode,
            as_of_date=as_of_date,
            price_basis=price_basis,
            volume_semantics=volume_semantics,
            missing_bar_policy="preserve-gaps",
            horizons=horizons,
            horizon_ranges=_horizon_ranges(bars, horizons),
            warnings=tuple(warnings),
            bars=bars,
            latest_final_date=latest_final_date,
            provisional_date=provisional.trade_date if provisional else None,
            provisional_source=provisional.source if provisional else None,
            provisional_provider_time=provisional.provider_time if provisional else None,
        )

    def _adjust_stock_prices(
        self, symbol: str, rows: list[_SourceBar], as_of_date: date
    ) -> tuple[list[_SourceBar], str, AnalysisInputWarning | None]:
        factors = self._store.get_adjustment_factors(
            symbol, rows[0].trade_date, as_of_date
        )
        by_date = {item.trade_date: item.factor for item in factors}
        final_dates = [row.trade_date for row in rows if not row.provisional]
        missing = [item for item in final_dates if item not in by_date]
        if not factors or missing:
            return rows, "raw", AnalysisInputWarning(
                code="adjustment_factors_incomplete",
                severity="warning",
                message="Stock prices remain raw because causal adjustment factors are incomplete.",
                dates=tuple(missing[:10]),
                count=len(missing) if factors else len(final_dates),
            )
        anchor = factors[-1].factor
        adjusted = [
            _scale_bar(row, by_date.get(row.trade_date, anchor) / anchor)
            for row in rows
        ]
        return adjusted, "forward-adjusted-as-of", None

    def _missing_bar_warning(
        self,
        symbol: str,
        kind: InstrumentKind | None,
        rows: list[_SourceBar],
        trading_dates: list[date],
        as_of_date: date,
    ) -> AnalysisInputWarning | None:
        expected = {item for item in trading_dates if item <= as_of_date}
        listed_on, delisted_on = self._store.get_instrument_lifecycle(symbol)
        if listed_on is not None:
            expected = {item for item in expected if item >= listed_on}
        if delisted_on is not None:
            expected = {item for item in expected if item <= delisted_on}
        if not expected:
            return None
        actual = {item.trade_date for item in rows}
        missing = expected - actual
        if kind is InstrumentKind.STOCK and missing:
            statuses = self._store.get_stock_trade_statuses(
                symbol, min(expected), as_of_date
            )
            suspended = {
                item.trade_date for item in statuses if item.status == "suspended"
            }
            missing -= suspended
        if not missing:
            return None
        ordered = tuple(sorted(missing))
        return AnalysisInputWarning(
            code="unexplained_missing_bars",
            severity="warning",
            message="Open trading dates without bars are preserved as gaps.",
            dates=ordered[:10],
            count=len(ordered),
        )


def _stored_bar(row: StoredDailyBar) -> _SourceBar:
    return _SourceBar(
        trade_date=row.trade_date,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
        source=row.source,
        provisional=False,
        observed_at_ms=row.updated_at_ms,
    )


def _provisional_bar(row: ProvisionalDailyBar) -> _SourceBar:
    return _SourceBar(
        trade_date=row.trade_date,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
        source=row.source,
        provisional=True,
        observed_at_ms=_timestamp_ms(row.received_at),
    )


def _scale_bar(row: _SourceBar, scale: float) -> _SourceBar:
    return _SourceBar(
        trade_date=row.trade_date,
        open=row.open * scale,
        high=row.high * scale,
        low=row.low * scale,
        close=row.close * scale,
        volume=row.volume,
        source=row.source,
        provisional=row.provisional,
        observed_at_ms=row.observed_at_ms,
    )


def _aggregate(
    rows: Sequence[_SourceBar],
    timeframe: AnalysisTimeframe,
    trading_dates: Sequence[date],
) -> list[AnalysisBar]:
    if not rows:
        return []
    ordered = sorted(rows, key=lambda row: row.trade_date)
    if timeframe is AnalysisTimeframe.DAILY:
        return [_analysis_bar((row,), not row.provisional) for row in ordered]

    groups: list[list[_SourceBar]] = []
    active_key: tuple[int, int] | None = None
    for row in ordered:
        key = _period_key(row.trade_date, timeframe)
        if key != active_key:
            groups.append([])
            active_key = key
        groups[-1].append(row)
    return [
        _analysis_bar(
            tuple(group),
            _aggregate_period_complete(group, timeframe, trading_dates, index < len(groups) - 1),
        )
        for index, group in enumerate(groups)
    ]


def _period_key(value: date, timeframe: AnalysisTimeframe) -> tuple[int, int]:
    if timeframe is AnalysisTimeframe.WEEKLY:
        iso = value.isocalendar()
        return iso.year, iso.week
    if timeframe is AnalysisTimeframe.MONTHLY:
        return value.year, value.month
    raise ValueError(f"unsupported aggregate timeframe: {timeframe}")


def _analysis_bar(rows: tuple[_SourceBar, ...], period_complete: bool) -> AnalysisBar:
    first, last = rows[0], rows[-1]
    return AnalysisBar(
        period_start=first.trade_date,
        period_end=last.trade_date,
        open=first.open,
        high=max(row.high for row in rows),
        low=min(row.low for row in rows),
        close=last.close,
        volume=sum(row.volume for row in rows),
        sources=tuple(dict.fromkeys(row.source for row in rows)),
        contains_provisional=any(row.provisional for row in rows),
        period_complete=period_complete,
        observed_at_ms=max(row.observed_at_ms for row in rows),
    )


def _aggregate_period_complete(
    rows: Sequence[_SourceBar],
    timeframe: AnalysisTimeframe,
    trading_dates: Sequence[date],
    has_later_group: bool,
) -> bool:
    if any(row.provisional for row in rows):
        return False
    if has_later_group:
        return True
    last_date = rows[-1].trade_date
    next_dates = [item for item in trading_dates if item > last_date]
    return bool(
        next_dates
        and _period_key(next_dates[0], timeframe)
        != _period_key(last_date, timeframe)
    )


def _daily_read_limit(timeframe: AnalysisTimeframe, long_horizon: int) -> int:
    multiplier = {
        AnalysisTimeframe.DAILY: 1,
        AnalysisTimeframe.WEEKLY: 6,
        AnalysisTimeframe.MONTHLY: 24,
    }[timeframe]
    buffer = {
        AnalysisTimeframe.DAILY: 20,
        AnalysisTimeframe.WEEKLY: 60,
        AnalysisTimeframe.MONTHLY: 72,
    }[timeframe]
    return min(10_000, long_horizon * multiplier + buffer)


def _horizon_ranges(
    bars: Sequence[AnalysisBar], horizons: AnalysisHorizons
) -> tuple[AnalysisHorizonRange, ...]:
    return tuple(
        AnalysisHorizonRange(
            name=name,
            requested_bars=count,
            available_bars=min(len(bars), count),
            start_date=(
                bars[-count].period_start
                if bars and len(bars) >= count
                else (bars[0].period_start if bars else None)
            ),
            end_date=bars[-1].period_end if bars else None,
        )
        for name, count in (
            ("short", horizons.short),
            ("medium", horizons.medium),
            ("long", horizons.long),
        )
    )


def _volume_capability(
    kind: InstrumentKind | None,
) -> tuple[str, AnalysisInputWarning | None]:
    semantics = {
        InstrumentKind.STOCK: "shares",
        InstrumentKind.ETF: "fund-shares",
        InstrumentKind.INDEX: "provider-defined-index-volume",
        InstrumentKind.SECTOR: "provider-defined-board-volume",
        InstrumentKind.CUSTOM_INDEX: "unweighted-constituent-share-sum",
    }.get(kind, "unknown")
    if kind in {InstrumentKind.STOCK, InstrumentKind.ETF}:
        return semantics, None
    return semantics, AnalysisInputWarning(
        code="volume_not_cross_symbol_comparable",
        severity="info",
        message="Volume semantics are instrument-specific and must not be compared across symbols.",
    )


def _timestamp_ms(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)
