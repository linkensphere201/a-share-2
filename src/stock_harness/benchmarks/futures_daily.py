"""Repeatable local-pipeline benchmark for a long futures continuous series."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date, datetime, timedelta
import json
import math
import statistics
import time
from zoneinfo import ZoneInfo

from stock_harness.futures_continuous_build import build_futures_continuous_series
from stock_harness.models import (
    FuturesBarState,
    FuturesContinuousSeries,
    FuturesContract,
    FuturesDailyBar,
    FuturesExchange,
    FuturesLifecycleStatus,
    FuturesPriceBasis,
    FuturesProduct,
    FuturesRollMapping,
    FuturesSeriesKind,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore


SOURCE = "synthetic-futures-benchmark"
CHINA_TIME = ZoneInfo("Asia/Shanghai")


def run(years: int = 30, trading_days_per_contract: int = 21, samples: int = 30) -> dict[str, object]:
    if years < 1 or trading_days_per_contract < 2 or samples < 1:
        raise ValueError("invalid futures benchmark dimensions")
    product, contracts, series, mappings, bars = _fixture(
        years, trading_days_per_contract
    )
    first_day = bars[0].trading_day
    last_day = bars[-1].trading_day
    next_day = _next_weekday(last_day)
    latest_contract = contracts[-1]

    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_futures_catalog(SOURCE, [product], contracts, [series])
        store.upsert_futures_roll_mappings(SOURCE, mappings)

        started = time.perf_counter()
        for offset in range(0, len(bars), 2_000):
            store.upsert_futures_daily_bars(SOURCE, bars[offset:offset + 2_000])
        initial_ingestion_ms = _elapsed_ms(started)

        latest_bar = bars[-1]
        no_change_ms = _sample(
            samples,
            lambda: store.upsert_futures_daily_bars(SOURCE, [latest_bar]),
        )
        increment = _bar(latest_contract.symbol, next_day, latest_bar.close + 0.5)
        started = time.perf_counter()
        store.upsert_futures_daily_bars(SOURCE, [increment])
        one_day_increment_ms = _elapsed_ms(started)

        started = time.perf_counter()
        first_build = build_futures_continuous_series(
            store, SOURCE, first_day, next_day, force=True,
        )
        continuous_full_build_ms = _elapsed_ms(started)

        corrected = replace(increment, close=increment.close + 0.25)
        store.upsert_futures_daily_bars(SOURCE, [corrected])
        started = time.perf_counter()
        suffix_build = build_futures_continuous_series(
            store, SOURCE, first_day, next_day,
        )
        continuous_suffix_build_ms = _elapsed_ms(started)

        observed_day = _next_weekday(next_day)
        observed_at = datetime.combine(
            observed_day, datetime.min.time(), CHINA_TIME
        ).replace(hour=14, minute=30)
        provisional = replace(
            _bar(latest_contract.symbol, observed_day, corrected.close + 0.5),
            state=FuturesBarState.PROVISIONAL,
            source="synthetic-intraday",
            provider_time=observed_at,
        )
        provisional_ms = _sample(
            samples,
            lambda: (
                store.upsert_futures_provisional_daily_bars(
                    provisional.source, [provisional], observed_at
                ),
                store.list_fused_futures_daily_bars(
                    series.symbol, first_day, observed_day
                ),
            ),
        )

        started = time.perf_counter()
        continuous_rows = store.list_futures_daily_bars(
            series.symbol, first_day, next_day
        )
        cold_history_read_ms = _elapsed_ms(started)
        warm_history_read_ms = _sample(
            samples,
            lambda: store.list_futures_daily_bars(
                series.symbol, first_day, next_day
            ),
        )

    return {
        "years": years,
        "contracts": len(contracts),
        "canonical_rows": len(bars) + 1,
        "continuous_rows": len(continuous_rows),
        "initial_ingestion_ms": round(initial_ingestion_ms, 3),
        "no_change_increment_ms": _summary(no_change_ms),
        "one_day_increment_ms": round(one_day_increment_ms, 3),
        "provisional_refresh_and_fused_read_ms": _summary(provisional_ms),
        "continuous_full_build_ms": round(continuous_full_build_ms, 3),
        "continuous_full_build_rows": first_build.rows_written,
        "continuous_suffix_build_ms": round(continuous_suffix_build_ms, 3),
        "continuous_suffix_build_rows": suffix_build.rows_written,
        "cold_history_read_ms": round(cold_history_read_ms, 3),
        "warm_history_read_ms": _summary(warm_history_read_ms),
        "scope": "local SQLite pipeline only; Provider and browser timings are separate",
    }


def _fixture(years: int, days_per_contract: int):
    product = FuturesProduct(
        "FUTPROD:SHFE:BM", "BM", "Benchmark", FuturesExchange.SHFE,
        None, 10, "contract", "CNY/unit",
    )
    series = FuturesContinuousSeries(
        "FUTCONT:SHFE:BM:MAIN:raw", "BM.SHF", product.symbol,
        "Benchmark main", FuturesExchange.SHFE, FuturesSeriesKind.MAIN,
        "MAIN", FuturesPriceBasis.RAW, "benchmark-v1",
    )
    contracts: list[FuturesContract] = []
    mappings: list[FuturesRollMapping] = []
    bars: list[FuturesDailyBar] = []
    current = date(1996, 1, 2)
    for index in range(years * 12):
        days = _weekdays(current, days_per_contract)
        month_index = (1996 * 12) + index
        contract_month = f"{month_index // 12:04d}{month_index % 12 + 1:02d}"
        provider_symbol = f"BM{contract_month[2:]}.SHF"
        symbol = f"FUT:SHFE:BM:{contract_month}"
        contract = FuturesContract(
            symbol, provider_symbol, product.symbol,
            f"Benchmark {contract_month}", FuturesExchange.SHFE,
            contract_month, days[0],
            _next_weekday(days[-1]), _next_weekday(days[-1]),
            None, 10, "contract", "CNY/unit",
            FuturesLifecycleStatus.DELISTED,
        )
        contracts.append(contract)
        mappings.append(FuturesRollMapping(
            series.symbol, series.provider_symbol, days[0],
            symbol, provider_symbol,
        ))
        for day_index, trading_day in enumerate(days):
            bars.append(_bar(
                symbol, trading_day,
                1000 + index * 0.8 + day_index * 0.05,
            ))
        current = _next_weekday(days[-1])
    return product, contracts, series, mappings, bars


def _bar(symbol: str, trading_day: date, close: float) -> FuturesDailyBar:
    return FuturesDailyBar(
        symbol, trading_day, trading_day, close - 0.2, close + 0.5,
        close - 0.5, close, close - 0.1, close, close - 0.1,
        10_000, 100_000_000, 50_000, 100, None, SOURCE,
        FuturesBarState.FINAL,
    )


def _weekdays(start: date, count: int) -> list[date]:
    result: list[date] = []
    current = start
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


def _next_weekday(value: date) -> date:
    current = value + timedelta(days=1)
    while current.weekday() >= 5:
        current += timedelta(days=1)
    return current


def _sample(count: int, operation) -> list[float]:
    values = []
    for _ in range(count):
        started = time.perf_counter()
        operation()
        values.append(_elapsed_ms(started))
    return values


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    p95 = ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]
    return {
        "p50": round(statistics.median(ordered), 3),
        "p95": round(p95, 3),
        "max": round(ordered[-1], 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, default=30)
    parser.add_argument("--days-per-contract", type=int, default=21)
    parser.add_argument("--samples", type=int, default=30)
    args = parser.parse_args()
    print(json.dumps(
        run(args.years, args.days_per_contract, args.samples),
        ensure_ascii=True, indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
