from datetime import date

import pytest

from stock_harness.analysis_inputs import AnalysisHorizons, AnalysisTimeframe
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
