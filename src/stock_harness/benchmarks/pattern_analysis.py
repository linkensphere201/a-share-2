"""Repeatable bounded-memory benchmark for unified pattern-analysis profiles."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import math
import statistics
import time
import tracemalloc

from stock_harness.analysis_inputs import AnalysisHorizons, AnalysisTimeframe
from stock_harness.models import (
    AdjustmentFactor,
    DailyBar,
    Instrument,
    InstrumentKind,
    StoredDailyBar,
)
from stock_harness.pattern_analysis import PatternAnalysisRequest, PatternAnalysisService
from stock_harness.sqlite_store import SQLiteMarketDataStore


def run(
    *,
    scan_symbols: int = 2200,
    scan_days: int = 260,
    full_days: int = 1250,
    full_runs: int = 3,
) -> dict[str, object]:
    if min(scan_symbols, scan_days, full_days, full_runs) < 1:
        raise ValueError("benchmark dimensions must be positive")
    scan_bars = _stored_bars("SCAN", _weekdays(date(2025, 1, 2), scan_days))
    tracemalloc.start()
    started = time.perf_counter()
    for _ in range(scan_symbols):
        PatternAnalysisService.scan_daily(scan_bars)
    scan_ms = (time.perf_counter() - started) * 1000
    _, scan_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    full_timings: list[float] = []
    full_peak = 0
    dates = _weekdays(date(2019, 1, 2), full_days)
    with SQLiteMarketDataStore(":memory:") as store:
        symbol = "000001.SZ"
        store.upsert_instruments([
            Instrument(symbol, "Benchmark", InstrumentKind.STOCK, "SZ")
        ])
        stored = _stored_bars(symbol, dates)
        store.upsert_daily_bars("benchmark", [
            DailyBar(
                symbol, item.trade_date, item.open, item.high, item.low,
                item.close, item.volume,
            )
            for item in stored
        ])
        store.upsert_adjustment_factors("benchmark", [
            AdjustmentFactor(symbol, item.trade_date, 1.0) for item in stored
        ])
        store.upsert_trading_dates("benchmark", dates)
        service = PatternAnalysisService(store)
        tracemalloc.start()
        for index in range(full_runs):
            started = time.perf_counter()
            service.analyze(PatternAnalysisRequest(
                symbol=symbol,
                timeframes=(AnalysisTimeframe.DAILY,),
                horizons=AnalysisHorizons(14, 28, min(1000, full_days)),
                config_version=f"benchmark-v{index}",
                as_of_date=dates[-1],
            ))
            full_timings.append((time.perf_counter() - started) * 1000)
        _, full_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    return {
        "scan": {
            "symbols": scan_symbols,
            "days_per_symbol": scan_days,
            "wall_ms": round(scan_ms, 3),
            "per_symbol_ms": round(scan_ms / scan_symbols, 6),
            "peak_mib": round(scan_peak / 1024 / 1024, 3),
        },
        "full": {
            "runs": full_runs,
            "days": full_days,
            "median_ms": round(statistics.median(full_timings), 3),
            "max_ms": round(max(full_timings), 3),
            "peak_mib": round(full_peak / 1024 / 1024, 3),
        },
    }


def _stored_bars(symbol: str, dates: list[date]) -> tuple[StoredDailyBar, ...]:
    bars = []
    for index, trade_date in enumerate(dates):
        trend = 10 + index * 0.002
        close = trend + math.sin(index / 9) * 0.7 + math.sin(index / 31) * 0.4
        bars.append(StoredDailyBar(
            symbol, trade_date, close - 0.08, close + 0.25, close - 0.25,
            close, 100_000 + (index % 20) * 3_000, "benchmark", index,
        ))
    return tuple(bars)


def _weekdays(start: date, count: int) -> list[date]:
    values: list[date] = []
    current = start
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current)
        current += timedelta(days=1)
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan-symbols", type=int, default=2200)
    parser.add_argument("--scan-days", type=int, default=260)
    parser.add_argument("--full-days", type=int, default=1250)
    parser.add_argument("--full-runs", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(run(
        scan_symbols=args.scan_symbols,
        scan_days=args.scan_days,
        full_days=args.full_days,
        full_runs=args.full_runs,
    ), ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
