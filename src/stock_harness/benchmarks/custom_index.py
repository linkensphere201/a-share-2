"""Repeatable in-memory benchmark for custom-index build and materialized reads."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import statistics
import time
import tracemalloc

from stock_harness.models import AdjustmentFactor, DailyBar, Instrument, InstrumentKind
from stock_harness.sqlite_store import SQLiteMarketDataStore


def run(member_count: int, day_count: int, read_count: int) -> dict[str, object]:
    if not 1 <= member_count <= 500 or day_count < 2 or read_count < 1:
        raise ValueError("invalid benchmark dimensions")
    symbols = [f"{index:06d}.SZ" for index in range(1, member_count + 1)]
    dates = _weekdays(date(2000, 1, 3), day_count)
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_instruments([
            Instrument(symbol, symbol, InstrumentKind.STOCK, "SZ") for symbol in symbols
        ])
        store.upsert_trading_dates("benchmark", dates)
        bars = []
        factors = []
        for day_index, trade_date in enumerate(dates):
            for member_index, symbol in enumerate(symbols):
                close = 10 + member_index * 0.01 + day_index * 0.001
                bars.append(DailyBar(
                    symbol, trade_date, close - 0.02, close + 0.05,
                    close - 0.05, close, 100_000 + member_index,
                ))
                factors.append(AdjustmentFactor(symbol, trade_date, 1.0))
        store.upsert_daily_bars("benchmark", bars)
        store.upsert_adjustment_factors("benchmark", factors)
        store.create_custom_index(
            "benchmark", "Benchmark", "", dates[0], 1000, "equal",
            [{"symbol": symbol} for symbol in symbols],
        )

        tracemalloc.start()
        started = time.perf_counter()
        store.rebuild_custom_index("benchmark")
        build_ms = (time.perf_counter() - started) * 1000
        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        timings = []
        for _ in range(read_count):
            started = time.perf_counter()
            rows = store.get_daily_bars("CINDEX:BENCHMARK")
            timings.append((time.perf_counter() - started) * 1000)
        return {
            "members": member_count,
            "days": day_count,
            "materialized_rows": len(rows),
            "build_ms": round(build_ms, 3),
            "build_peak_mib": round(peak_bytes / 1024 / 1024, 3),
            "read_median_ms": round(statistics.median(timings), 3),
            "read_p95_ms": round(_percentile(timings, 0.95), 3),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--members", type=int, default=50)
    parser.add_argument("--days", type=int, default=1000)
    parser.add_argument("--reads", type=int, default=30)
    args = parser.parse_args()
    print(json.dumps(run(args.members, args.days, args.reads), ensure_ascii=True))
    return 0


def _weekdays(start: date, count: int) -> list[date]:
    result = []
    current = start
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * quantile))]


if __name__ == "__main__":
    raise SystemExit(main())
