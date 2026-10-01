"""Synthetic, repeatable cold/warm baseline. Never opens the user's database."""
from datetime import date, timedelta
from dataclasses import replace
import json
from time import perf_counter
from stock_harness.models import Instrument, InstrumentKind, DailyBar, AdjustmentFactor
from stock_harness.pattern_analysis import PatternAnalysisRequest, PatternAnalysisService
from stock_harness.analysis_inputs import AnalysisTimeframe
from stock_harness.screener_strategies import DEFAULT_HORIZONS, get_strategy, strategy_definitions
from stock_harness.performance import snapshot
from stock_harness.sqlite_store import SQLiteMarketDataStore


def main():
    with SQLiteMarketDataStore(":memory:") as store:
        symbol = "000001.SZ"
        store.upsert_instruments([Instrument(symbol, "Synthetic", InstrumentKind.STOCK, "SZ")])
        days = [date(2025, 1, 1) + timedelta(days=i) for i in range(500)]
        prices = [20 - i * .01 if i < 470 else 15.3 + (i % 3) * .02 for i in range(500)]
        store.upsert_daily_bars("fixture", [DailyBar(symbol, day, p, p + .05, p - .05, p, 1000)
                                           for day, p in zip(days, prices)])
        store.upsert_adjustment_factors("fixture", [AdjustmentFactor(symbol, day, 1) for day in days])
        store.upsert_trading_dates("fixture", days)
        service = PatternAnalysisService(store)
        for mode in ("cold", "warm"):
            before = snapshot()
            started = perf_counter()
            for definition in strategy_definitions():
                strategy = get_strategy(definition["strategy_id"])
                if strategy.execution != "shape":
                    continue
                request = PatternAnalysisRequest(symbol=symbol, timeframes=(AnalysisTimeframe.DAILY,),
                    horizons=DEFAULT_HORIZONS, config_version=strategy.analysis_config,
                    include_preview=False, as_of_date=days[-1])
                strategy.analyze(service, request)
            # Force two complete graph publications even when the synthetic shape
            # gates reject, so the baseline also covers detection and persistence.
            for config in ("benchmark-full-a", "benchmark-full-b"):
                service.analyze(replace(request, config_version=config))
            after = snapshot()
            print(json.dumps({"mode": mode, "elapsed_ms": (perf_counter() - started) * 1000,
                "stages": {key: {field: value[field] - before.get(key, {}).get(field, 0)
                    for field in ("count", "total_ms")} for key, value in after.items()}}, sort_keys=True))


if __name__ == "__main__":
    main()
