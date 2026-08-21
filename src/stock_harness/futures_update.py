"""Correction-aware canonical futures startup increment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import logging
from collections.abc import Sequence

from stock_harness.futures_backfill import (
    futures_calendar_digest,
    futures_daily_digest,
    fetch_futures_daily_result,
    persist_futures_daily_batches,
)
from stock_harness.futures_provider import TushareFuturesProvider
from stock_harness.models import FuturesExchange
from stock_harness.sqlite_store import SQLiteMarketDataStore


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FuturesIncrementResult:
    exchanges: int
    contracts_checked: int
    contracts_updated: int
    rows_changed: int
    mappings_written: int
    rejected_daily_rows: int
    errors: tuple[str, ...]


def run_futures_increment(
    provider: TushareFuturesProvider,
    store: SQLiteMarketDataStore,
    exchanges: Sequence[FuturesExchange],
    completed_through: date,
    correction_window_trading_days: int,
) -> FuturesIncrementResult:
    if correction_window_trading_days < 0:
        raise ValueError("futures correction window must be non-negative")
    selected = tuple(dict.fromkeys(exchanges))
    errors: list[str] = []
    checked = updated = changed = mappings_written = 0
    rejected_daily_rows = 0
    calendar_start = completed_through - timedelta(
        days=max(31, correction_window_trading_days * 3 + 14)
    )
    LOGGER.info(
        "futures_increment_started exchanges=%d completed_through=%s correction_days=%d",
        len(selected), completed_through, correction_window_trading_days,
    )

    for exchange in selected:
        try:
            catalog = provider.discover_exchange(exchange, completed_through)
            store.upsert_futures_catalog(
                provider.code, catalog.products, catalog.contracts,
                catalog.continuous_series,
            )
            calendar = provider.calendar(exchange, calendar_start, completed_through)
            store.upsert_futures_calendar(provider.code, calendar)
            store.record_futures_update_receipt(
                provider.code, "calendar", exchange.value, completed_through,
                len(calendar), futures_calendar_digest(calendar),
                "complete" if calendar else "empty",
            )
        except Exception as error:
            errors.append(
                f"{exchange.value} catalog/calendar: {type(error).__name__}"
            )
            _log_bounded_failure(
                len(errors), "exchange", exchange, exchange.value, error
            )
            continue

        open_days = sorted(
            item.calendar_date for item in calendar
            if item.is_open and item.calendar_date <= completed_through
        )
        if not open_days:
            errors.append(f"{exchange.value} calendar: no completed trading days")
            continue
        correction_start = (
            open_days[-correction_window_trading_days]
            if correction_window_trading_days > 0
            and len(open_days) >= correction_window_trading_days
            else open_days[0] if correction_window_trading_days > 0 else open_days[-1]
        )

        for contract in catalog.contracts:
            fetch_end = min(completed_through, contract.last_trading_date)
            if fetch_end < correction_start or fetch_end < contract.listed_on:
                continue
            checked += 1
            state = store.get_futures_sync_state(
                provider.code, "daily", exchange.value, contract.symbol
            )
            if correction_window_trading_days == 0 and state is not None:
                fetch_start = max(
                    contract.listed_on, state.covered_through + timedelta(days=1)
                )
            else:
                missing_start = (
                    state.covered_through + timedelta(days=1)
                    if state else correction_start
                )
                fetch_start = max(
                    contract.listed_on, min(correction_start, missing_start)
                )
            if fetch_start > fetch_end:
                continue
            try:
                fetch = fetch_futures_daily_result(
                    provider, contract, fetch_start, fetch_end
                )
                bars = fetch.bars
                rejected_daily_rows += len(fetch.rejections)
                changed += persist_futures_daily_batches(provider.code, store, bars)
                store.checkpoint_futures_sync(
                    provider.code, "daily", exchange.value, contract.symbol,
                    fetch_start, fetch_end, len(bars),
                )
                store.record_futures_update_receipt(
                    provider.code, "daily", contract.symbol, fetch_end,
                    len(bars), futures_daily_digest(bars),
                    (
                        "partial" if fetch.rejections else
                        "complete" if bars else "empty"
                    ),
                    (
                        f"rejected_rows={len(fetch.rejections)}"
                        if fetch.rejections else ""
                    ),
                )
                updated += 1
            except Exception as error:
                errors.append(
                    f"{contract.symbol} final increment: {type(error).__name__}"
                )
                _log_bounded_failure(
                    len(errors), "contract", exchange, contract.symbol, error
                )

        for series in catalog.continuous_series:
            try:
                mappings = provider.fetch_roll_mappings(
                    series, catalog.contracts, correction_start, completed_through
                )
                store.replace_futures_roll_mapping_window(
                    provider.code, exchange.value, series.symbol,
                    correction_start, completed_through, mappings,
                )
                mappings_written += len(mappings)
            except Exception as error:
                errors.append(
                    f"{series.symbol} mapping increment: {type(error).__name__}"
                )
                _log_bounded_failure(
                    len(errors), "mapping", exchange, series.symbol, error
                )

    result = FuturesIncrementResult(
        exchanges=len(selected), contracts_checked=checked,
        contracts_updated=updated, rows_changed=changed,
        mappings_written=mappings_written,
        rejected_daily_rows=rejected_daily_rows, errors=tuple(errors),
    )
    LOGGER.info(
        "futures_increment_completed exchanges=%d contracts_checked=%d contracts_updated=%d rows_changed=%d mappings=%d rejected_rows=%d errors=%d",
        result.exchanges, result.contracts_checked, result.contracts_updated,
        result.rows_changed, result.mappings_written,
        result.rejected_daily_rows, len(result.errors),
    )
    return result


def _log_bounded_failure(
    count: int,
    operation: str,
    exchange: FuturesExchange,
    symbol: str,
    error: Exception,
) -> None:
    log = LOGGER.warning if count <= 10 or count % 100 == 0 else LOGGER.debug
    log(
        "futures_increment_item_failed operation=%s exchange=%s symbol=%s error_type=%s error_count=%d",
        operation, exchange.value, symbol, type(error).__name__, count,
    )
