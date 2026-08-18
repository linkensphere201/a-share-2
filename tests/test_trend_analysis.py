from datetime import date, timedelta

from stock_harness.analysis_inputs import AnalysisHorizons, AnalysisTimeframe
from stock_harness.models import AdjustmentFactor, DailyBar, Instrument, InstrumentKind
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.trend_analysis import TrendAnalysisService


def _store() -> tuple[SQLiteMarketDataStore, list[date]]:
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("000001.SZ", "Ping An Bank", InstrumentKind.STOCK, "SZ")
    ])
    start = date(2026, 7, 1)
    days = [start + timedelta(days=index) for index in range(12)]
    closes = [10, 9, 8, 9, 10, 11, 10, 9, 8, 9, 10, 11]
    store.upsert_daily_bars("tushare", [
        DailyBar("000001.SZ", day, close, close + 0.2, close - 0.2, close, 100)
        for day, close in zip(days, closes)
    ])
    store.upsert_adjustment_factors("tushare", [
        AdjustmentFactor("000001.SZ", day, 1) for day in days
    ])
    store.upsert_trading_dates("tushare", days)
    return store, days


def test_explicit_recalculate_registers_and_persists_only_requested_timeframes():
    store, days = _store()
    try:
        results = TrendAnalysisService(store).recalculate(
            "000001.SZ",
            [AnalysisTimeframe.DAILY, AnalysisTimeframe.WEEKLY],
            AnalysisHorizons(3, 6, 10),
            config_version="settings-1",
            include_preview=False,
            as_of_date=days[-1],
        )

        assert [item["status"] for item in results] == ["succeeded", "succeeded"]
        assert all(item["items"] for item in results)
        assert store.get_latest_generated_analysis_run(
            "000001.SZ", "trend", "monthly"
        ) is None
        assert store.claim_generated_analysis_targets() == []
    finally:
        store.close()


def test_repeated_explicit_recalculate_reuses_identical_success():
    store, days = _store()
    service = TrendAnalysisService(store)
    try:
        first = service.recalculate(
            "000001.SZ", [AnalysisTimeframe.DAILY], AnalysisHorizons(3, 6, 10),
            config_version="settings-1", include_preview=False,
            as_of_date=days[-1],
        )[0]
        repeated = service.recalculate(
            "000001.SZ", [AnalysisTimeframe.DAILY], AnalysisHorizons(3, 6, 10),
            config_version="settings-1", include_preview=False,
            as_of_date=days[-1],
        )[0]

        assert repeated["run_id"] == first["run_id"]
        assert repeated["input_digest"] == first["input_digest"]
    finally:
        store.close()


def test_failed_explicit_recalculate_leaves_retryable_target_without_result():
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_instruments([
            Instrument("000001.SZ", "Ping An Bank", InstrumentKind.STOCK, "SZ")
        ])

        try:
            TrendAnalysisService(store).recalculate(
                "000001.SZ", [AnalysisTimeframe.DAILY], AnalysisHorizons(3, 6, 10),
                config_version="settings-1", include_preview=False,
                as_of_date=date(2026, 8, 18),
            )
            raise AssertionError("missing input must fail")
        except ValueError as error:
            assert "no analysis bars" in str(error)

        assert store.get_latest_generated_analysis_run(
            "000001.SZ", "trend", "daily"
        ) is None
        assert len(store.claim_generated_analysis_targets()) == 1
