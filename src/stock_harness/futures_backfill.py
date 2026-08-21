"""Resumable canonical futures history and mapping backfill."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
import json
import logging
import time
from collections.abc import Sequence

from stock_harness.futures_provider import (
    FuturesDailyFetchResult,
    TushareFuturesProvider,
)
from stock_harness.models import FuturesContract, FuturesDailyBar, FuturesExchange
from stock_harness.sqlite_store import SQLiteMarketDataStore


LOGGER = logging.getLogger(__name__)
FUTURES_CALENDAR_LOOKAHEAD_DAYS = 14


@dataclass(frozen=True, slots=True)
class FuturesBackfillResult:
    exchanges: int
    contracts_discovered: int
    contracts_skipped: int
    contract_windows_completed: int
    daily_rows_changed: int
    mappings_written: int
    rejected_daily_rows: int
    errors: tuple[str, ...]
    coverage: tuple[dict[str, object], ...]


def run_futures_backfill(
    provider: TushareFuturesProvider,
    store: SQLiteMarketDataStore,
    exchanges: Sequence[FuturesExchange],
    start_date: date,
    end_date: date,
    *,
    as_of: date | None = None,
    max_contracts: int | None = None,
) -> FuturesBackfillResult:
    if start_date > end_date:
        raise ValueError("futures backfill start must not exceed end")
    if max_contracts is not None and max_contracts < 1:
        raise ValueError("futures backfill max_contracts must be positive")
    as_of = as_of or end_date
    errors: list[str] = []
    discovered = skipped = completed = changed = mappings_written = processed = 0
    rejected_daily_rows = 0
    started = time.perf_counter()
    selected_exchanges = tuple(dict.fromkeys(exchanges))
    LOGGER.info(
        "futures_backfill_started exchanges=%d start=%s end=%s max_contracts=%s",
        len(selected_exchanges), start_date, end_date, max_contracts,
    )

    for exchange in selected_exchanges:
        exchange_started = time.perf_counter()
        try:
            catalog = provider.discover_exchange(exchange, as_of)
            store.upsert_futures_catalog(
                provider.code, catalog.products, catalog.contracts,
                catalog.continuous_series,
            )
            calendar_end = end_date + timedelta(
                days=FUTURES_CALENDAR_LOOKAHEAD_DAYS
            )
            calendar = provider.calendar(exchange, start_date, calendar_end)
            store.upsert_futures_calendar(provider.code, calendar)
            store.record_futures_update_receipt(
                provider.code, "calendar", exchange.value, calendar_end,
                len(calendar), futures_calendar_digest(calendar),
                "complete" if calendar else "empty",
            )
        except Exception as error:
            LOGGER.error(
                "futures_backfill_exchange_failed exchange=%s error_type=%s",
                exchange.value, type(error).__name__,
            )
            errors.append(
                f"{exchange.value} catalog/calendar: {type(error).__name__}"
            )
            continue

        discovered += len(catalog.contracts)
        LOGGER.info(
            "futures_backfill_exchange_catalog_ready exchange=%s products=%d contracts=%d series=%d calendar_rows=%d",
            exchange.value, len(catalog.products), len(catalog.contracts),
            len(catalog.continuous_series), len(calendar),
        )
        for contract in catalog.contracts:
            desired_start = max(start_date, contract.listed_on)
            desired_end = min(end_date, contract.last_trading_date)
            if desired_start > desired_end:
                skipped += 1
                continue
            if max_contracts is not None and processed >= max_contracts:
                break
            state = store.get_futures_sync_state(
                provider.code, "daily", exchange.value, contract.symbol
            )
            windows = _missing_windows(
                desired_start, desired_end,
                state.covered_from if state else None,
                state.covered_through if state else None,
            )
            if not windows:
                skipped += 1
                continue
            processed += 1
            for window_start, window_end in windows:
                try:
                    fetch = fetch_futures_daily_result(
                        provider, contract, window_start, window_end
                    )
                    bars = fetch.bars
                    rejected_daily_rows += len(fetch.rejections)
                    stats = persist_futures_daily_batches(provider.code, store, bars)
                    store.checkpoint_futures_sync(
                        provider.code, "daily", exchange.value, contract.symbol,
                        window_start, window_end, len(bars),
                    )
                    store.record_futures_update_receipt(
                        provider.code, "daily", contract.symbol, window_end,
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
                    completed += 1
                    changed += stats
                except Exception as error:
                    errors.append(
                        f"{contract.symbol} {window_start}..{window_end}: "
                        f"{type(error).__name__}"
                    )
                    _log_bounded_failure(
                        len(errors), "contract", exchange, contract.symbol, error
                    )
            if processed and processed % 100 == 0:
                LOGGER.info(
                    "futures_backfill_progress processed_contracts=%d completed_windows=%d changed_rows=%d errors=%d",
                    processed, completed, changed, len(errors),
                )

        for series in catalog.continuous_series:
            try:
                mappings = provider.fetch_roll_mappings(
                    series, catalog.contracts, start_date, end_date
                )
                store.replace_futures_roll_mapping_window(
                    provider.code, exchange.value, series.symbol,
                    start_date, end_date, mappings,
                )
                mappings_written += len(mappings)
            except Exception as error:
                errors.append(
                    f"{series.symbol} mapping: {type(error).__name__}"
                )
                _log_bounded_failure(
                    len(errors), "mapping", exchange, series.symbol, error
                )
        LOGGER.info(
            "futures_backfill_exchange_completed exchange=%s processed_contracts=%d changed_rows=%d mappings=%d errors=%d duration_ms=%.3f",
            exchange.value, processed, changed, mappings_written, len(errors),
            (time.perf_counter() - exchange_started) * 1000,
        )

    result = FuturesBackfillResult(
        exchanges=len(selected_exchanges),
        contracts_discovered=discovered,
        contracts_skipped=skipped,
        contract_windows_completed=completed,
        daily_rows_changed=changed,
        mappings_written=mappings_written,
        rejected_daily_rows=rejected_daily_rows,
        errors=tuple(errors),
        coverage=tuple(store.list_futures_coverage()),
    )
    LOGGER.info(
        "futures_backfill_completed exchanges=%d processed_contracts=%d completed_windows=%d changed_rows=%d mappings=%d rejected_rows=%d errors=%d duration_ms=%.3f",
        result.exchanges, processed, completed, changed, mappings_written,
        rejected_daily_rows, len(errors),
        (time.perf_counter() - started) * 1000,
    )
    return result


def fetch_futures_daily_result(
    provider: TushareFuturesProvider,
    contract: FuturesContract,
    start_date: date,
    end_date: date,
) -> FuturesDailyFetchResult:
    fetch_result = getattr(provider, "fetch_contract_daily_result", None)
    if fetch_result is not None:
        return fetch_result(contract, start_date, end_date)
    return FuturesDailyFetchResult(
        tuple(provider.fetch_contract_daily(contract, start_date, end_date)), ()
    )


def _log_bounded_failure(
    count: int,
    operation: str,
    exchange: FuturesExchange,
    symbol: str,
    error: Exception,
) -> None:
    log = LOGGER.warning if count <= 10 or count % 100 == 0 else LOGGER.debug
    log(
        "futures_backfill_item_failed operation=%s exchange=%s symbol=%s error_type=%s error_count=%d",
        operation, exchange.value, symbol, type(error).__name__, count,
    )


def _missing_windows(
    desired_start: date,
    desired_end: date,
    covered_from: date | None,
    covered_through: date | None,
) -> tuple[tuple[date, date], ...]:
    if covered_from is None or covered_through is None:
        return ((desired_start, desired_end),)
    windows: list[tuple[date, date]] = []
    if desired_start < covered_from:
        windows.append((desired_start, min(desired_end, covered_from - timedelta(days=1))))
    if desired_end > covered_through:
        windows.append((max(desired_start, covered_through + timedelta(days=1)), desired_end))
    return tuple((start, end) for start, end in windows if start <= end)


def persist_futures_daily_batches(
    source: str,
    store: SQLiteMarketDataStore,
    bars: Sequence[FuturesDailyBar],
) -> int:
    changed = 0
    for offset in range(0, len(bars), 2_000):
        changed += store.upsert_futures_daily_bars(
            source, bars[offset:offset + 2_000]
        ).changed
    return changed


def futures_daily_digest(bars: Sequence[FuturesDailyBar]) -> bytes:
    payload = [
        [item.symbol, item.trading_day.isoformat(), item.open, item.high,
         item.low, item.close, item.volume_contracts, item.settlement,
         item.open_interest_contracts]
        for item in sorted(bars, key=lambda value: (value.symbol, value.trading_day))
    ]
    return _digest(payload)


def futures_calendar_digest(days: Sequence[object]) -> bytes:
    payload = [repr(item) for item in days]
    return _digest(payload)


def _digest(payload: object) -> bytes:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).digest()
