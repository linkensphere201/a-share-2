from dataclasses import replace
from datetime import date, timedelta
import json

import pytest
from fastapi.testclient import TestClient

from stock_harness.analysis_inputs import AnalysisHorizons, AnalysisTimeframe
from stock_harness.api import create_app
from stock_harness.models import AdjustmentFactor, DailyBar, Instrument, InstrumentKind
from stock_harness.pattern_analysis import (
    PatternAnalysisProfile,
    PatternAnalysisRequest,
    PatternAnalysisService,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore


def test_pattern_analysis_request_validates_shared_entry_contract() -> None:
    request = PatternAnalysisRequest(
        symbol="000001.SZ",
        timeframes=(AnalysisTimeframe.DAILY,),
        horizons=AnalysisHorizons(14, 28, 250),
        config_version="test-v1",
        as_of_date=date(2026, 9, 4),
    )

    request.validate()
    assert request.profile is PatternAnalysisProfile.FULL


def test_scan_profile_cannot_silently_fall_back_to_full_persistence() -> None:
    store = SQLiteMarketDataStore(":memory:")
    try:
        service = PatternAnalysisService(store)
        request = PatternAnalysisRequest(
            symbol="000001.SZ",
            timeframes=(AnalysisTimeframe.DAILY,),
            horizons=AnalysisHorizons(14, 28, 250),
            config_version="test-v1",
            profile=PatternAnalysisProfile.SCAN,
        )
        with pytest.raises(ValueError, match="scan profile"):
            service.analyze(request)
    finally:
        store.close()


def test_service_http_and_snapshot_share_identical_structural_output() -> None:
    store = SQLiteMarketDataStore(":memory:")
    start = date.today() - timedelta(days=79)
    days = [start + timedelta(days=index) for index in range(80)]
    closes = [10 + ((index % 16) - 8) * .12 + index * .01 for index in range(80)]
    store.upsert_instruments([
        Instrument("000001.SZ", "Cross Entry", InstrumentKind.STOCK, "SZ")
    ])
    store.upsert_daily_bars("tushare", [
        DailyBar(
            "000001.SZ", day, close, close + .3, close - .3, close,
            1_000 + index,
        )
        for index, (day, close) in enumerate(zip(days, closes))
    ])
    store.upsert_adjustment_factors("tushare", [
        AdjustmentFactor("000001.SZ", day, 1) for day in days
    ])
    store.upsert_trading_dates("tushare", days)
    request = PatternAnalysisRequest(
        symbol="000001.SZ",
        timeframes=(AnalysisTimeframe.DAILY,),
        horizons=AnalysisHorizons(14, 28, 60),
        config_version="cross-entry-v1",
        as_of_date=days[-1],
    )
    try:
        direct = PatternAnalysisService(store).analyze(request)[0]
        snapshot = PatternAnalysisService(store).build_snapshot(
            replace(request, profile=PatternAnalysisProfile.REPLAY),
            timeframe=AnalysisTimeframe.DAILY,
        )
        with TestClient(create_app(store)) as client:
            response = client.post("/api/analysis/trend/recalculate", json={
                "symbol": "000001.SZ",
                "timeframes": ["daily"],
                "short_horizon_bars": 14,
                "medium_horizon_bars": 28,
                "long_horizon_bars": 60,
                "config_version": "cross-entry-v1",
                "include_preview": False,
            })
        assert response.status_code == 200
        through_http = response.json()["results"][0]
        assert through_http["run_id"] == direct["run_id"]
        assert through_http["items"] == direct["items"]
        assert json.loads(json.dumps(snapshot["items"], sort_keys=True)) == direct["items"]
    finally:
        store.close()
