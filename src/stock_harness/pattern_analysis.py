"""Authoritative product entry point for M4 pattern analysis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from stock_harness.analysis_inputs import AnalysisHorizons, AnalysisTimeframe
from stock_harness.models import StoredDailyBar
from stock_harness.pattern_analysis_scan import DailyStructureScan, scan_daily_structure
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.trend_analysis import TrendAnalysisService
from stock_harness.trend_pivots import DirectionalChangeConfig


class PatternAnalysisProfile(StrEnum):
    """Execution budget; profiles never redefine structural semantics."""

    SCAN = "scan"
    FULL = "full"
    REPLAY = "replay"


@dataclass(frozen=True, slots=True)
class PatternAnalysisRequest:
    symbol: str
    timeframes: tuple[AnalysisTimeframe, ...]
    horizons: AnalysisHorizons
    config_version: str
    include_preview: bool = False
    as_of_date: date | None = None
    profile: PatternAnalysisProfile = PatternAnalysisProfile.FULL
    pivot_config: DirectionalChangeConfig = DirectionalChangeConfig()

    def validate(self) -> None:
        if not self.symbol.strip():
            raise ValueError("pattern-analysis symbol is required")
        if not self.timeframes:
            raise ValueError("pattern-analysis requires at least one timeframe")
        if not self.config_version.strip():
            raise ValueError("pattern-analysis config version is required")
        self.horizons.validate()


class PatternAnalysisService:
    """Stable facade used by product features instead of detector modules."""

    def __init__(self, store: SQLiteMarketDataStore) -> None:
        self._delegate = TrendAnalysisService(store)

    @staticmethod
    def scan_daily(bars: tuple[StoredDailyBar, ...] | list[StoredDailyBar]) -> DailyStructureScan:
        return scan_daily_structure(bars)

    def analyze(self, request: PatternAnalysisRequest) -> list[dict[str, object]]:
        request.validate()
        if request.profile is PatternAnalysisProfile.SCAN:
            raise ValueError("scan profile is not available through persisted analysis yet")
        return self._delegate.recalculate(
            request.symbol,
            request.timeframes,
            request.horizons,
            config_version=request.config_version,
            include_preview=request.include_preview,
            as_of_date=request.as_of_date,
            pivot_config=request.pivot_config,
        )

    def build_snapshot(
        self,
        request: PatternAnalysisRequest,
        *,
        timeframe: AnalysisTimeframe,
    ) -> dict[str, object]:
        request.validate()
        if request.as_of_date is None:
            raise ValueError("snapshot analysis requires an as-of date")
        return self._delegate.build_review_snapshot(
            request.symbol,
            timeframe,
            request.horizons,
            as_of_date=request.as_of_date,
            config_version=request.config_version,
            pivot_config=request.pivot_config,
        )
