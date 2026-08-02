"""Persistent, asynchronous repair channel for missing Tushare daily snapshots."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from stock_harness.ports import DailyBarValidationProvider, TradeDateRepairProvider
from stock_harness.sqlite_store import SQLiteMarketDataStore


@dataclass(frozen=True, slots=True)
class RepairResult:
    job_id: int
    trade_date: date
    status: str
    expected_rows: int
    repaired_rows: int
    unresolved_rows: int


def repair_tushare_stock_date(
    store: SQLiteMarketDataStore,
    trade_date: date,
    universe_provider: TradeDateRepairProvider,
    fallback_providers: Sequence[DailyBarValidationProvider],
    targeted_providers: Sequence[DailyBarValidationProvider] = (),
) -> RepairResult:
    job_id = store.enqueue_repair_job("tushare", "stock", trade_date)
    targeted_symbols = store.list_unresolved_repair_symbols(job_id)
    if targeted_symbols:
        return _repair_targeted_symbols(
            store, job_id, trade_date, targeted_symbols, targeted_providers
        )
    if store.has_daily_snapshot("tushare", "stock", trade_date):
        store.finish_repair_job(job_id, "completed", 0, 0, 0)
        return RepairResult(job_id, trade_date, "completed", 0, 0, 0)

    job_id = store.begin_repair_job("tushare", "stock", trade_date)
    try:
        batch = universe_provider.fetch_stock_trade_date(trade_date)
        if not batch.instruments:
            raise RuntimeError("repair universe provider returned no trading A-share instruments")
        store.ensure_instruments(batch.instruments)
        expected = {instrument.symbol for instrument in batch.instruments}
        provider_bars = {bar.symbol: bar for bar in batch.bars if bar.symbol in expected}
        if provider_bars:
            store.upsert_daily_bars(universe_provider.code, tuple(provider_bars.values()))
            store.record_repair_items(
                job_id,
                universe_provider.code,
                "repaired",
                {symbol: "daily OHLCV repaired" for symbol in provider_bars},
            )

        unresolved = expected - provider_bars.keys()
        unresolved_messages = {
            symbol: f"{universe_provider.code} returned no usable daily bar"
            for symbol in unresolved
        }
        repaired_count = len(provider_bars)
        for fallback in fallback_providers:
            fallback_bars = {}
            for symbol in sorted(unresolved):
                try:
                    bars = fallback.fetch_symbol_daily_bars(symbol, trade_date, trade_date)
                    bar = next((item for item in bars if item.trade_date == trade_date), None)
                    if bar is None:
                        unresolved_messages[symbol] = f"{fallback.code} returned no daily bar"
                    else:
                        fallback_bars[symbol] = bar
                except Exception as exc:  # A failed symbol must not abort the repair date.
                    unresolved_messages[symbol] = f"{fallback.code}: {type(exc).__name__}: {exc}"
            if fallback_bars:
                store.upsert_daily_bars(fallback.code, tuple(fallback_bars.values()))
                store.record_repair_items(
                    job_id,
                    fallback.code,
                    "repaired",
                    {symbol: "daily OHLCV repaired" for symbol in fallback_bars},
                )
                repaired_count += len(fallback_bars)
                unresolved.difference_update(fallback_bars)

        if unresolved:
            store.record_repair_items(
                job_id,
                None,
                "unresolved",
                {symbol: unresolved_messages[symbol] for symbol in unresolved},
            )
        status = "completed" if not unresolved else "partial"
        store.finish_repair_job(
            job_id, status, len(expected), repaired_count, len(unresolved)
        )
        return RepairResult(
            job_id, trade_date, status, len(expected), repaired_count, len(unresolved)
        )
    except Exception as exc:
        store.finish_repair_job(job_id, "failed", 0, 0, 0, f"{type(exc).__name__}: {exc}")
        return RepairResult(job_id, trade_date, "failed", 0, 0, 0)


def _repair_targeted_symbols(
    store: SQLiteMarketDataStore,
    job_id: int,
    trade_date: date,
    symbols: Sequence[str],
    providers: Sequence[DailyBarValidationProvider],
) -> RepairResult:
    store.begin_repair_job("tushare", "stock", trade_date)
    unresolved = set(symbols)
    messages = {symbol: "no fallback Provider returned a daily bar" for symbol in symbols}
    repaired = 0
    for provider in providers:
        provider_bars = {}
        for symbol in sorted(unresolved):
            try:
                bars = provider.fetch_symbol_daily_bars(symbol, trade_date, trade_date)
                bar = next((item for item in bars if item.trade_date == trade_date), None)
                if bar is not None:
                    bar.validate()
                    provider_bars[symbol] = bar
                else:
                    messages[symbol] = f"{provider.code} returned no daily bar"
            except Exception as exc:
                messages[symbol] = f"{provider.code}: {type(exc).__name__}: {exc}"
        if provider_bars:
            store.upsert_daily_bars(provider.code, tuple(provider_bars.values()))
            store.record_repair_items(
                job_id,
                provider.code,
                "repaired",
                {symbol: "invalid primary row replaced" for symbol in provider_bars},
            )
            repaired += len(provider_bars)
            unresolved.difference_update(provider_bars)
    if unresolved:
        store.record_repair_items(
            job_id, None, "unresolved", {symbol: messages[symbol] for symbol in unresolved}
        )
    status = "completed" if not unresolved else "partial"
    store.finish_repair_job(job_id, status, len(symbols), repaired, len(unresolved))
    if not unresolved:
        store.resolve_provider_incident(
            "tushare",
            "daily_ohlcv",
            "stock",
            trade_date,
            "invalid_daily_bar",
            f"invalid primary rows replaced by fallback Providers: {repaired}",
        )
    return RepairResult(job_id, trade_date, status, len(symbols), repaired, len(unresolved))
