"""Resumable full-market daily OHLCV backfill."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from stock_harness.models import Instrument, InstrumentKind
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


def run_stock_backfill(
    provider: TushareDailyProvider,
    store: SQLiteMarketDataStore,
    start_date: date,
    end_date: date,
    max_dates: int | None = None,
    progress_every: int = 10,
) -> BackfillResult:
    instruments = provider.list_instruments()
    store.upsert_instruments(instruments)
    known_symbols = {instrument.symbol for instrument in instruments}
    trading_dates = provider.trading_dates(start_date, end_date)
    snapshot_dates = store.list_daily_snapshot_dates(
        provider.code, "stock", start_date, end_date
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
        if trade_date in snapshot_dates:
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


def years_ago(today: date, years: int) -> date:
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)


def _exchange(symbol: str) -> str:
    return symbol.rsplit(".", 1)[-1] if "." in symbol else "UNKNOWN"
