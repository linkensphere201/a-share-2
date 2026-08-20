"""Resumable canonical futures history and mapping backfill."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
import json
import logging
from collections.abc import Sequence

from stock_harness.futures_provider import TushareFuturesProvider
from stock_harness.models import FuturesDailyBar, FuturesExchange
from stock_harness.sqlite_store import SQLiteMarketDataStore


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FuturesBackfillResult:
    exchanges: int
    contracts_discovered: int
    contracts_skipped: int
    contract_windows_completed: int
    daily_rows_changed: int
    mappings_written: int
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

    for exchange in tuple(dict.fromkeys(exchanges)):
        try:
            catalog = provider.discover_exchange(exchange, as_of)
            store.upsert_futures_catalog(
                provider.code, catalog.products, catalog.contracts,
                catalog.continuous_series,
            )
            calendar = provider.calendar(exchange, start_date, end_date)
            store.upsert_futures_calendar(provider.code, calendar)
            store.record_futures_update_receipt(
                provider.code, "calendar", exchange.value, end_date,
                len(calendar), _calendar_digest(calendar),
                "complete" if calendar else "empty",
            )
        except Exception as error:
            LOGGER.exception("futures_backfill_exchange_failed exchange=%s", exchange.value)
            errors.append(f"{exchange.value} catalog/calendar: {error}")
            continue

        discovered += len(catalog.contracts)
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
                    bars = provider.fetch_contract_daily(
                        contract, window_start, window_end
                    )
                    stats = _persist_daily_batches(provider.code, store, bars)
                    store.checkpoint_futures_sync(
                        provider.code, "daily", exchange.value, contract.symbol,
                        window_start, window_end, len(bars),
                    )
                    store.record_futures_update_receipt(
                        provider.code, "daily", contract.symbol, window_end,
                        len(bars), _daily_digest(bars),
                        "complete" if bars else "empty",
                    )
                    completed += 1
                    changed += stats
                except Exception as error:
                    LOGGER.exception(
                        "futures_backfill_contract_failed exchange=%s symbol=%s start=%s end=%s",
                        exchange.value, contract.symbol, window_start, window_end,
                    )
                    errors.append(
                        f"{contract.symbol} {window_start}..{window_end}: {error}"
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
                LOGGER.exception(
                    "futures_backfill_mapping_failed exchange=%s symbol=%s",
                    exchange.value, series.symbol,
                )
                errors.append(f"{series.symbol} mapping: {error}")

    return FuturesBackfillResult(
        exchanges=len(tuple(dict.fromkeys(exchanges))),
        contracts_discovered=discovered,
        contracts_skipped=skipped,
        contract_windows_completed=completed,
        daily_rows_changed=changed,
        mappings_written=mappings_written,
        errors=tuple(errors),
        coverage=tuple(store.list_futures_coverage()),
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


def _persist_daily_batches(
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


def _daily_digest(bars: Sequence[FuturesDailyBar]) -> bytes:
    payload = [
        [item.symbol, item.trading_day.isoformat(), item.open, item.high,
         item.low, item.close, item.volume_contracts, item.settlement,
         item.open_interest_contracts]
        for item in sorted(bars, key=lambda value: (value.symbol, value.trading_day))
    ]
    return _digest(payload)


def _calendar_digest(days: Sequence[object]) -> bytes:
    payload = [repr(item) for item in days]
    return _digest(payload)


def _digest(payload: object) -> bytes:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).digest()
