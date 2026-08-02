"""Benchmark the SQLite daily-update hot path with representative workloads."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from datetime import date, timedelta
from pathlib import Path

from stock_harness.models import DailyBar, Instrument, InstrumentKind
from stock_harness.sqlite_store import SQLiteMarketDataStore


def run_benchmark(output: Path, instrument_count: int, history_years: int, update_days: int) -> dict[str, object]:
    output.parent.mkdir(parents=True, exist_ok=True)
    instruments = _instruments(instrument_count)
    source = "synthetic-benchmark"
    setup_started = time.perf_counter()

    with SQLiteMarketDataStore(output) as store:
        store.upsert_instruments(instruments)
        focal_symbol = instruments[0].symbol
        history_dates = _weekdays(date(2000, 1, 3), history_years * 250)
        history_stats = store.upsert_daily_bars(
            source,
            [_bar(focal_symbol, trade_date, offset) for offset, trade_date in enumerate(history_dates)],
        )
        setup_ms = (time.perf_counter() - setup_started) * 1000

        next_date = history_dates[-1] + timedelta(days=1)
        update_dates = _weekdays(next_date, update_days)
        update_samples: list[float] = []
        latest_batch: list[DailyBar] = []
        for day_offset, trade_date in enumerate(update_dates, start=len(history_dates)):
            latest_batch = [
                _bar(instrument.symbol, trade_date, day_offset + instrument_offset)
                for instrument_offset, instrument in enumerate(instruments)
            ]
            update_samples.append(
                store.upsert_daily_snapshot(source, "stock", trade_date, latest_batch).elapsed_ms
            )

        no_change_samples = [
            store.upsert_daily_snapshot(source, "stock", update_dates[-1], latest_batch).elapsed_ms
            for _ in range(20)
        ]
        query_samples: list[float] = []
        query_rows = 0
        for _ in range(100):
            started = time.perf_counter()
            query_rows = len(store.get_daily_bars(focal_symbol))
            query_samples.append((time.perf_counter() - started) * 1000)

        result = {
            "database": str(output.resolve()),
            "database_size_bytes": output.stat().st_size,
            "instrument_count": instrument_count,
            "history_years": history_years,
            "history_rows": history_stats.received,
            "update_days": update_days,
            "rows_per_market_update": instrument_count,
            "query_rows": query_rows,
            "total_rows": store.count_daily_bars(),
            "setup_ms": round(setup_ms, 3),
            "market_update_ms": _summary(update_samples),
            "no_change_ms": _summary(no_change_samples),
            "long_history_query_ms": _summary(query_samples),
        }
    return result


def _instruments(count: int) -> list[Instrument]:
    return [
        Instrument(
            symbol=f"{index:06d}.SH",
            name=f"Synthetic {index}",
            kind=InstrumentKind.STOCK,
            exchange="SH",
        )
        for index in range(1, count + 1)
    ]


def _weekdays(start: date, count: int) -> list[date]:
    result: list[date] = []
    current = start
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


def _bar(symbol: str, trade_date: date, offset: int) -> DailyBar:
    close = 10.0 + (offset % 1000) / 100.0
    return DailyBar(
        symbol=symbol,
        trade_date=trade_date,
        open=close - 0.05,
        high=close + 0.10,
        low=close - 0.10,
        close=close,
        volume=1_000_000 + offset,
    )


def _summary(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "min": round(ordered[0], 3),
        "median": round(statistics.median(ordered), 3),
        "p95": round(ordered[p95_index], 3),
        "max": round(ordered[-1], 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--instruments", type=int, default=5_500)
    parser.add_argument("--history-years", type=int, default=30)
    parser.add_argument("--update-days", type=int, default=20)
    args = parser.parse_args()
    result = run_benchmark(args.output, args.instruments, args.history_years, args.update_days)
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
