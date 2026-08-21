"""Atomic persistence semantics shared by futures backfill and increments."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from hashlib import sha256
import json

from stock_harness.futures_provider import FuturesDailyFetchResult
from stock_harness.models import FuturesContract, FuturesDailyBar, FuturesExchange
from stock_harness.sqlite_store import SQLiteMarketDataStore


def persist_futures_daily_result(
    source: str,
    store: SQLiteMarketDataStore,
    exchange: FuturesExchange,
    contract: FuturesContract,
    start_date: date,
    end_date: date,
    fetch: FuturesDailyFetchResult,
) -> int:
    """Persist bars and evidence, advancing coverage only through a valid prefix."""
    changed = persist_futures_daily_batches(source, store, fetch.bars)
    checkpoint_end = _safe_checkpoint_end(start_date, end_date, fetch)
    if checkpoint_end is not None:
        completed_rows = sum(
            start_date <= bar.trading_day <= checkpoint_end for bar in fetch.bars
        )
        store.checkpoint_futures_sync(
            source,
            "daily",
            exchange.value,
            contract.symbol,
            start_date,
            checkpoint_end,
            completed_rows,
        )
    store.record_futures_update_receipt(
        source,
        "daily",
        contract.symbol,
        end_date,
        len(fetch.bars),
        futures_daily_digest(fetch.bars),
        "partial" if fetch.rejections else "complete" if fetch.bars else "empty",
        f"rejected_rows={len(fetch.rejections)}" if fetch.rejections else "",
    )
    return changed


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
    return _digest([repr(item) for item in days])


def _safe_checkpoint_end(
    start_date: date,
    end_date: date,
    fetch: FuturesDailyFetchResult,
) -> date | None:
    rejected_dates = [
        item.trading_day
        for item in fetch.rejections
        if start_date <= item.trading_day <= end_date
    ]
    if not rejected_dates:
        return end_date
    candidate = min(rejected_dates) - timedelta(days=1)
    return candidate if candidate >= start_date else None


def _digest(payload: object) -> bytes:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).digest()
