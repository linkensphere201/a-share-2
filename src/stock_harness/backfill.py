"""Resumable full-market daily OHLCV backfill."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from stock_harness.models import Instrument, InstrumentKind
from stock_harness.ports import DailyBarValidationProvider
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.tushare_provider import TushareDailyProvider


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BackfillResult:
    trading_dates: int
    skipped_dates: int
    completed_dates: int
    rows_written: int
    discovered_instruments: int
    empty_dates: int


@dataclass(frozen=True, slots=True)
class SymbolBackfillResult:
    configured_symbols: int
    skipped_symbols: int
    completed_symbols: int
    fetched_rows: int
    changed_rows: int


def run_stock_backfill(
    provider: TushareDailyProvider,
    store: SQLiteMarketDataStore,
    start_date: date,
    end_date: date,
    max_dates: int | None = None,
    progress_every: int = 10,
    refresh_last_trading_days: int = 0,
) -> BackfillResult:
    instruments = provider.list_instruments()
    store.upsert_instruments(instruments)
    known_symbols = {instrument.symbol for instrument in instruments}
    trading_dates = provider.trading_dates(start_date, end_date)
    snapshot_dates = store.list_daily_snapshot_dates(
        provider.code, "stock", start_date, end_date
    )
    forced_dates = set(
        trading_dates[-refresh_last_trading_days:]
        if refresh_last_trading_days > 0
        else ()
    )
    skipped = 0
    completed = 0
    rows_written = 0
    discovered_instruments = 0
    empty_dates = 0
    processed = 0
    LOGGER.info(
        "backfill_plan instruments=%d trading_dates=%d start=%s end=%s",
        len(instruments),
        len(trading_dates),
        start_date,
        end_date,
    )
    for trade_date in trading_dates:
        if trade_date in snapshot_dates and trade_date not in forced_dates:
            skipped += 1
            continue
        if max_dates is not None and processed >= max_dates:
            break
        bars = provider.fetch_daily_bars(trade_date)
        if not bars:
            store.record_provider_incident(
                provider.code,
                "daily_ohlcv",
                "stock",
                trade_date,
                "empty_open_trade_date",
                "provider returned no daily bars for an open trading date",
            )
            store.record_coverage_gap(
                provider.code,
                "stock",
                trade_date,
                "provider returned no daily bars for an open trading date",
            )
            empty_dates += 1
            processed += 1
            LOGGER.warning("coverage_gap trade_date=%s reason=provider_empty", trade_date)
            continue
        valid_bars = []
        invalid_bars = []
        for bar in bars:
            try:
                bar.validate()
            except ValueError as exc:
                invalid_bars.append((bar, str(exc)))
            else:
                valid_bars.append(bar)
        bars = valid_bars
        store.resolve_provider_incident(
            provider.code,
            "daily_ohlcv",
            "stock",
            trade_date,
            "empty_open_trade_date",
            f"provider retry returned {len(bars)} daily bars",
        )
        missing_symbols = sorted({bar.symbol for bar in bars} - known_symbols)
        if missing_symbols:
            store.upsert_instruments(
                [
                    Instrument(
                        symbol=symbol,
                        name=symbol,
                        kind=InstrumentKind.STOCK,
                        exchange=_exchange(symbol),
                        active=False,
                    )
                    for symbol in missing_symbols
                ]
            )
            known_symbols.update(missing_symbols)
            discovered_instruments += len(missing_symbols)
            LOGGER.warning(
                "historical_instruments_discovered trade_date=%s count=%d symbols=%s",
                trade_date,
                len(missing_symbols),
                ",".join(missing_symbols[:10]),
            )
        stats = store.upsert_daily_snapshot(provider.code, "stock", trade_date, bars)
        if invalid_bars:
            details = ", ".join(
                f"{bar.symbol}: {reason}" for bar, reason in invalid_bars[:10]
            )
            store.record_provider_incident(
                provider.code,
                "daily_ohlcv",
                "stock",
                trade_date,
                "invalid_daily_bar",
                details,
            )
            job_id = store.queue_symbol_repairs(
                provider.code,
                "stock",
                trade_date,
                {bar.symbol: reason for bar, reason in invalid_bars},
            )
            LOGGER.warning(
                "invalid_daily_bars trade_date=%s count=%d repair_job=%d symbols=%s",
                trade_date,
                len(invalid_bars),
                job_id,
                ",".join(bar.symbol for bar, _reason in invalid_bars[:10]),
            )
        completed += 1
        processed += 1
        rows_written += stats.changed
        if progress_every > 0 and completed % progress_every == 0:
            LOGGER.info(
                "backfill_progress completed=%d skipped=%d total=%d trade_date=%s rows_written=%d last_write_ms=%.3f",
                completed,
                skipped,
                len(trading_dates),
                trade_date,
                rows_written,
                stats.elapsed_ms,
            )
    result = BackfillResult(
        len(trading_dates), skipped, completed, rows_written, discovered_instruments, empty_dates
    )
    LOGGER.info(
        "backfill_result trading_dates=%d skipped_dates=%d completed_dates=%d rows_written=%d discovered_instruments=%d empty_dates=%d",
        result.trading_dates,
        result.skipped_dates,
        result.completed_dates,
        result.rows_written,
        result.discovered_instruments,
        result.empty_dates,
    )
    return result


def run_symbol_backfill(
    provider: TushareDailyProvider,
    store: SQLiteMarketDataStore,
    scope: str,
    kind: InstrumentKind,
    instruments: Sequence[Instrument],
    start_date: date,
    end_date: date,
    max_symbols: int | None = None,
    fallback_provider: DailyBarValidationProvider | None = None,
    allow_empty_initial: bool = False,
    instrument_start_dates: Mapping[str, date] | None = None,
    allow_unrepaired_rejections: bool = False,
    force_refresh_from: date | None = None,
) -> SymbolBackfillResult:
    if not scope:
        raise ValueError("symbol backfill scope is required")
    if start_date > end_date:
        raise ValueError("symbol backfill start must not exceed end")
    if any(instrument.kind is not kind for instrument in instruments):
        raise ValueError("symbol backfill instruments must match the requested kind")
    store.upsert_instruments(instruments)
    open_dates = set(provider.trading_dates(start_date, end_date))
    skipped = 0
    completed = 0
    fetched_rows = 0
    changed_rows = 0
    for instrument in instruments:
        desired_start = max(
            start_date,
            (instrument_start_dates or {}).get(instrument.symbol, start_date),
        )
        state = store.get_symbol_sync_state(provider.code, scope, instrument.symbol)
        forced = force_refresh_from is not None
        if (
            not forced
            and
            state is not None
            and state.covered_from <= desired_start
            and state.covered_through >= end_date
        ):
            skipped += 1
            continue
        if max_symbols is not None and completed >= max_symbols:
            break
        fetch_from = desired_start
        fetch_through = end_date
        if forced:
            fetch_from = max(desired_start, force_refresh_from)
        elif state is not None and desired_start < state.covered_from:
            fetch_through = state.covered_from - timedelta(days=1)
        elif state is not None:
            fetch_from = max(desired_start, state.covered_through + timedelta(days=1))
        bars = provider.fetch_symbol_daily_bars(
            kind, instrument.symbol, fetch_from, fetch_through
        )
        rejected = provider.rejected_bars
        fallback_bars = []
        if rejected:
            for item in rejected:
                store.record_provider_incident(
                    provider.code,
                    "daily_ohlcv",
                    f"{scope}:{instrument.symbol}",
                    item.trade_date,
                    "invalid_daily_bar",
                    item.reason,
                )
            if fallback_provider is None:
                if not allow_unrepaired_rejections:
                    raise RuntimeError(
                        f"provider returned {len(rejected)} invalid {scope} bars for "
                        f"{instrument.symbol} without a configured fallback"
                    )
            else:
                rejected_dates = {item.trade_date for item in rejected}
                candidates = fallback_provider.fetch_symbol_daily_bars(
                    instrument.symbol, min(rejected_dates), max(rejected_dates)
                )
                fallback_bars = [bar for bar in candidates if bar.trade_date in rejected_dates]
                for bar in fallback_bars:
                    bar.validate()
                covered_dates = {bar.trade_date for bar in fallback_bars}
                if covered_dates != rejected_dates:
                    missing = sorted(rejected_dates - covered_dates)
                    raise RuntimeError(
                        f"fallback {fallback_provider.code} did not repair {instrument.symbol}: "
                        + ", ".join(item.isoformat() for item in missing)
                    )
                store.upsert_daily_bars(fallback_provider.code, fallback_bars)
        if state is None and not bars and any(fetch_from <= item <= fetch_through for item in open_dates):
            message = f"provider returned no initial {scope} history for {instrument.symbol}"
            if not allow_empty_initial:
                raise RuntimeError(message)
            store.record_provider_incident(
                provider.code,
                "daily_ohlcv",
                f"{scope}:{instrument.symbol}",
                fetch_through,
                "empty_symbol_history",
                message,
            )
        stats = store.upsert_symbol_history(
            provider.code,
            scope,
            instrument.symbol,
            fetch_from,
            fetch_through,
            bars,
        )
        if fallback_provider is not None:
            for item in rejected:
                store.resolve_provider_incident(
                    provider.code,
                    "daily_ohlcv",
                    f"{scope}:{instrument.symbol}",
                    item.trade_date,
                    "invalid_daily_bar",
                    f"repaired from {fallback_provider.code}",
                )
        completed += 1
        fetched_rows += len(bars)
        changed_rows += stats.changed
        LOGGER.info(
            "symbol_backfill_progress scope=%s symbol=%s completed=%d total=%d "
            "fetch_from=%s end=%s fetched=%d changed=%d write_ms=%.3f",
            scope,
            instrument.symbol,
            completed,
            len(instruments),
            fetch_from,
            fetch_through,
            len(bars),
            stats.changed,
            stats.elapsed_ms,
        )
    result = SymbolBackfillResult(
        len(instruments), skipped, completed, fetched_rows, changed_rows
    )
    LOGGER.info(
        "symbol_backfill_result scope=%s configured=%d skipped=%d completed=%d "
        "fetched_rows=%d changed_rows=%d",
        scope,
        result.configured_symbols,
        result.skipped_symbols,
        result.completed_symbols,
        result.fetched_rows,
        result.changed_rows,
    )
    return result


def years_ago(today: date, years: int) -> date:
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)


def _exchange(symbol: str) -> str:
    return symbol.rsplit(".", 1)[-1] if "." in symbol else "UNKNOWN"
