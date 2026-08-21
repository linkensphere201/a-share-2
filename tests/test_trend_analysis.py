from datetime import date, timedelta

from stock_harness.analysis_inputs import AnalysisHorizons, AnalysisTimeframe
from stock_harness.models import (
    AdjustmentFactor,
    BoardMembership,
    DailyBar,
    FuturesBarState,
    FuturesCalendarDay,
    FuturesContinuousSeries,
    FuturesContract,
    FuturesDailyBar,
    FuturesExchange,
    FuturesLifecycleStatus,
    FuturesPriceBasis,
    FuturesProduct,
    FuturesSeriesKind,
    Instrument,
    InstrumentKind,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.trend_analysis import TrendAnalysisService, TrendAnalysisWorker


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


def _futures_store() -> tuple[
    SQLiteMarketDataStore, FuturesContract, tuple[FuturesContinuousSeries, ...], list[date]
]:
    store = SQLiteMarketDataStore(":memory:")
    product = FuturesProduct(
        "FUTPROD:SHFE:CU", "CU", "Copper", FuturesExchange.SHFE,
        None, 5, "contract", "CNY/tonne",
    )
    contract = FuturesContract(
        "FUT:SHFE:CU:202609", "CU2609.SHF", product.symbol, "Copper 2609",
        FuturesExchange.SHFE, "202609", date(2025, 9, 16), date(2026, 9, 15),
        date(2026, 9, 18), None, 5, "contract", "CNY/tonne",
        FuturesLifecycleStatus.TRADING,
    )
    series = tuple(
        FuturesContinuousSeries(
            f"FUTCONT:SHFE:CU:{variant}:{basis.value}", provider_symbol,
            product.symbol, f"Copper {basis.value}", FuturesExchange.SHFE,
            FuturesSeriesKind.MAIN, variant, basis, "mapping-v1",
        )
        for basis, variant, provider_symbol in (
            (FuturesPriceBasis.RAW, "MAIN", "CU.SHF"),
            (FuturesPriceBasis.BACKWARD_RATIO, "RATIO", "CU.RATIO.SHF"),
            (FuturesPriceBasis.BACKWARD_ADDITIVE, "ADDITIVE", "CU.ADD.SHF"),
        )
    )
    store.upsert_futures_catalog(
        "tushare-futures", [product], [contract], list(series)
    )
    start = date(2026, 7, 1)
    days = [start + timedelta(days=index) for index in range(24)]
    closes = [10, 9, 8, 9, 10, 11, 10, 9, 8, 9, 10, 11] * 2
    store.upsert_futures_calendar("tushare-futures", [
        FuturesCalendarDay(FuturesExchange.SHFE, day, True, None) for day in days
    ])
    rows = []
    for symbol in (contract.symbol, *(item.symbol for item in series)):
        rows.extend(FuturesDailyBar(
            symbol, day, day, close, close + 0.2, close - 0.2, close,
            close - 0.1, close, close - 0.1, 100 + index,
            10_000 + index, 1_000 + index, 1, None, "tushare-futures",
            FuturesBarState.FINAL,
            mapped_contract_symbol=(contract.symbol if symbol != contract.symbol else None),
        ) for index, (day, close) in enumerate(zip(days, closes)))
    store.upsert_futures_daily_bars("tushare-futures", rows)
    return store, contract, series, days


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
        assert {
            item["payload"]["horizon"]
            for item in results[0]["items"]
            if item["item_type"] == "anchor"
        } == {"short", "long"}
        assert any(item["item_type"] == "line" for item in results[0]["items"])
        assert any(item["item_type"] == "zone" for item in results[0]["items"])
        evidence = next(
            item for item in results[0]["items"]
            if item["item_id"] == "key-level-volume-profile-evidence"
        )
        assert "not exact position cost" in evidence["payload"]["uncertainty"]
        assert results[0]["algorithm_version"] == "trend-causal-replay-v18"
        pattern_items = [
            item for item in results[0]["items"] if item["item_type"] == "pattern"
        ]
        assert pattern_items
        assert sum(
            item["payload"]["primary"] is True for item in pattern_items
        ) == 1
        assert sorted(
            item["payload"]["interpretation_rank"] for item in pattern_items
        ) == list(range(1, len(pattern_items) + 1))
        assert any(
            item["item_type"] == "evidence"
            and item["payload"].get("kind") == "breakout-state-summary"
            for item in results[0]["items"]
        )
        assert any(
            item["item_type"] == "evidence"
            and item["payload"].get("kind") == "latest-structural-event-summary"
            for item in results[0]["items"]
        )
        context = next(
            item for item in results[0]["items"]
            if item["item_id"] == "market-board-context-evidence"
        )
        assert context["payload"]["subject"]["available"] is True
        assert context["payload"]["available_context_count"] == 0
        assert store.get_latest_generated_analysis_run(
            "000001.SZ", "trend", "monthly"
        ) is None
        assert store.claim_generated_analysis_targets() == []
    finally:
        store.close()


def test_real_and_supported_continuous_futures_run_full_analysis_and_review():
    store, contract, series, days = _futures_store()
    try:
        service = TrendAnalysisService(store)
        for symbol in (contract.symbol, *(item.symbol for item in series)):
            result = service.recalculate(
                symbol, [AnalysisTimeframe.DAILY], AnalysisHorizons(6, 12, 20),
                config_version="futures-v1", include_preview=False,
                as_of_date=days[-1],
            )[0]
            item_types = {item["item_type"] for item in result["items"]}
            assert {"anchor", "line", "zone", "pattern", "evidence"} <= item_types
            capabilities = next(
                item["payload"] for item in result["items"]
                if item["item_id"] == "analysis-input-capabilities"
            )
            assert capabilities["instrument"]["kind"].startswith("futures-")
            assert capabilities["volume_semantics"] == "contracts"
            assert capabilities["mapped_contracts"] == (
                [contract.symbol] if symbol != contract.symbol else []
            )

        snapshot = service.build_review_snapshot(
            series[-1].symbol, AnalysisTimeframe.DAILY, AnalysisHorizons(6, 12, 20),
            as_of_date=days[-1], config_version="futures-review-v1",
        )
        assert snapshot["run_id"].startswith("review-")
        assert snapshot["status"] == "succeeded"
        assert any(item["item_type"] == "pattern" for item in snapshot["items"])
        assert store.get_latest_generated_analysis_run(
            series[-1].symbol, "trend", "daily"
        )["run_id"] != snapshot["run_id"]
    finally:
        store.close()


def test_recalculate_combines_available_market_and_board_context_in_digest():
    store, days = _store()
    try:
        store.upsert_instruments([
            Instrument("399001.SZ", "Shenzhen Component", InstrumentKind.INDEX, "SZ"),
            Instrument("BK-BANK.DC", "Bank Board", InstrumentKind.SECTOR, "DC"),
        ])
        store.upsert_daily_bars("tushare", [
            DailyBar(symbol, day, close, close + 0.2, close - 0.2, close, 100)
            for symbol, offset in (("399001.SZ", 0.0), ("BK-BANK.DC", 1.0))
            for day, close in zip(days, [10 + offset + index * 0.1 for index in range(12)])
        ])
        store.replace_board_memberships(
            "tushare", "BK-BANK.DC", days[-1],
            [BoardMembership(
                "BK-BANK.DC", "000001.SZ", "Ping An Bank", "tushare", days[-1]
            )],
        )
        service = TrendAnalysisService(store)
        first = service.recalculate(
            "000001.SZ", [AnalysisTimeframe.DAILY], AnalysisHorizons(3, 6, 10),
            config_version="settings-context", include_preview=False,
            as_of_date=days[-1],
        )[0]
        context = next(
            item["payload"] for item in first["items"]
            if item["item_id"] == "market-board-context-evidence"
        )

        assert context["market"]["symbol"] == "399001.SZ"
        assert context["market"]["available"] is True
        assert context["related"][0]["symbol"] == "BK-BANK.DC"
        assert context["available_context_count"] == 2

        store.upsert_daily_bars("tushare", [
            DailyBar("BK-BANK.DC", days[-1], 13, 13.2, 8.8, 9, 200)
        ])
        changed = service.recalculate(
            "000001.SZ", [AnalysisTimeframe.DAILY], AnalysisHorizons(3, 6, 10),
            config_version="settings-context", include_preview=False,
            as_of_date=days[-1],
        )[0]

        assert changed["run_id"] != first["run_id"]
        assert changed["input_digest"] != first["input_digest"]
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


def test_worker_recalculates_only_previously_registered_dirty_target():
    store, days = _store()
    service = TrendAnalysisService(store)
    try:
        first = service.recalculate(
            "000001.SZ", [AnalysisTimeframe.DAILY], AnalysisHorizons(3, 6, 10),
            config_version="settings-1", include_preview=False,
            as_of_date=days[-1],
        )[0]
        next_day = days[-1] + timedelta(days=1)
        store.upsert_adjustment_factors("tushare", [
            AdjustmentFactor("000001.SZ", next_day, 1)
        ])
        store.upsert_trading_dates("tushare", [next_day])
        store.upsert_daily_bars("tushare", [
            DailyBar("000001.SZ", next_day, 11, 12, 10.5, 11.5, 120)
        ])

        processed = TrendAnalysisWorker(store).run_once()
        latest = store.get_latest_generated_analysis_run(
            "000001.SZ", "trend", "daily"
        )

        assert processed == 1
        assert latest is not None and latest["run_id"] != first["run_id"]
        assert latest["as_of_date"] == next_day
        assert latest["supersedes_run_id"] == first["run_id"]
        assert store.claim_generated_analysis_targets() == []
    finally:
        store.close()
