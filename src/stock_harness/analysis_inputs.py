"""Causal, chart-independent inputs for registered trading systems."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Protocol, Sequence

from stock_harness.models import ProvisionalDailyBar, StoredDailyBar


class AnalysisTimeframe(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class AnalysisInputMode(StrEnum):
    FINAL = "final"
    PREVIEW = "preview"


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
    observed_at_ms: int


@dataclass(frozen=True, slots=True)
class AnalysisInput:
    symbol: str
    timeframe: AnalysisTimeframe
    mode: AnalysisInputMode
    as_of_date: date
    price_basis: str
    missing_bar_policy: str
    bars: tuple[AnalysisBar, ...]
    latest_final_date: date | None
    provisional_date: date | None
    provisional_source: str | None
    provisional_provider_time: datetime | None


class AnalysisInputStore(Protocol):
    def get_daily_bars(
        self,
        symbol: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[StoredDailyBar]: ...

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

    def __init__(self, store: AnalysisInputStore) -> None:
        self._store = store

    def build(
        self,
        symbol: str,
        as_of_date: date,
        timeframe: AnalysisTimeframe = AnalysisTimeframe.DAILY,
        mode: AnalysisInputMode = AnalysisInputMode.FINAL,
    ) -> AnalysisInput:
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol:
            raise ValueError("analysis symbol is required")

        final_rows = self._store.get_daily_bars(
            normalized_symbol, end_date=as_of_date
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

        return AnalysisInput(
            symbol=normalized_symbol,
            timeframe=timeframe,
            mode=mode,
            as_of_date=as_of_date,
            price_basis="raw",
            missing_bar_policy="preserve-gaps",
            bars=tuple(_aggregate(source_bars, timeframe)),
            latest_final_date=latest_final_date,
            provisional_date=provisional.trade_date if provisional else None,
            provisional_source=provisional.source if provisional else None,
            provisional_provider_time=provisional.provider_time if provisional else None,
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


def _aggregate(
    rows: Sequence[_SourceBar], timeframe: AnalysisTimeframe
) -> list[AnalysisBar]:
    if not rows:
        return []
    ordered = sorted(rows, key=lambda row: row.trade_date)
    if timeframe is AnalysisTimeframe.DAILY:
        return [_analysis_bar((row,)) for row in ordered]

    groups: list[list[_SourceBar]] = []
    active_key: tuple[int, int] | None = None
    for row in ordered:
        key = _period_key(row.trade_date, timeframe)
        if key != active_key:
            groups.append([])
            active_key = key
        groups[-1].append(row)
    return [_analysis_bar(tuple(group)) for group in groups]


def _period_key(value: date, timeframe: AnalysisTimeframe) -> tuple[int, int]:
    if timeframe is AnalysisTimeframe.WEEKLY:
        iso = value.isocalendar()
        return iso.year, iso.week
    if timeframe is AnalysisTimeframe.MONTHLY:
        return value.year, value.month
    raise ValueError(f"unsupported aggregate timeframe: {timeframe}")


def _analysis_bar(rows: tuple[_SourceBar, ...]) -> AnalysisBar:
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
        observed_at_ms=max(row.observed_at_ms for row in rows),
    )


def _timestamp_ms(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)
