"""Authoritative product entry point for M4 pattern analysis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import NotRequired, TypedDict, cast

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


class PatternAnalysisResult(TypedDict):
    run_id: str
    status: str
    as_of_date: date
    algorithm_version: str
    config_version: str
    completion_state: str
    items: list[dict[str, object]]
    warnings: list[dict[str, object]]
    stale: bool
    failure_details: NotRequired[object | None]


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

    def analyze(self, request: PatternAnalysisRequest) -> list[PatternAnalysisResult]:
        request.validate()
        if request.profile is PatternAnalysisProfile.SCAN:
            raise ValueError("scan profile requires preloaded bars through scan_daily")
        if request.profile is PatternAnalysisProfile.REPLAY:
            if request.as_of_date is None:
                raise ValueError("replay profile requires an as-of date")
            return [
                self.build_snapshot(request, timeframe=timeframe)
                for timeframe in dict.fromkeys(request.timeframes)
            ]
        return cast(list[PatternAnalysisResult], self._delegate.recalculate(
            request.symbol,
            request.timeframes,
            request.horizons,
            config_version=request.config_version,
            include_preview=request.include_preview,
            as_of_date=request.as_of_date,
            pivot_config=request.pivot_config,
        ))

    def analyze_low_base_candidate(
        self, request: PatternAnalysisRequest,
    ) -> PatternAnalysisResult | None:
        """Reject absent structures cheaply; only full saved analysis can select a stock."""
        request.validate()
        if (request.timeframes != (AnalysisTimeframe.DAILY,)
                or request.include_preview or request.as_of_date is None
                or request.profile is not PatternAnalysisProfile.FULL):
            raise ValueError("low-base screening requires dated final daily full analysis")
        if not self._delegate.has_low_base_structure(
            request.symbol, request.as_of_date, request.horizons,
        ):
            return None
        return self.analyze(request)[0]

    def analyze_deep_drawdown_candidate(
        self, request: PatternAnalysisRequest,
    ) -> PatternAnalysisResult | None:
        request.validate()
        if (request.timeframes != (AnalysisTimeframe.DAILY,)
                or request.include_preview or request.as_of_date is None
                or request.profile is not PatternAnalysisProfile.FULL):
            raise ValueError("deep-drawdown screening requires dated final daily full analysis")
        if not self._delegate.has_deep_drawdown_structure(
            request.symbol, request.as_of_date, request.horizons,
        ):
            return None
        return self.analyze(request)[0]

    def analyze_screening_candidate(
        self, request: PatternAnalysisRequest, structure: str,
        *, periods: tuple[str, ...] = (), states: tuple[str, ...] = (),
    ) -> PatternAnalysisResult | None:
        """Negative-only gate; candidates still require the authoritative saved run."""
        request.validate()
        if (request.timeframes != (AnalysisTimeframe.DAILY,)
                or request.include_preview or request.as_of_date is None
                or request.profile is not PatternAnalysisProfile.FULL):
            raise ValueError("screening requires dated final daily full analysis")
        if structure not in {"bull-flag", "first-pullback", "major-descending"}:
            raise ValueError(f"unsupported screening structure: {structure}")
        if not self._delegate.has_screening_structure(
            request.symbol, request.as_of_date, request.horizons, structure,
            periods=periods, states=states,
        ):
            return None
        return self.analyze(request)[0]

    def build_snapshot(
        self,
        request: PatternAnalysisRequest,
        *,
        timeframe: AnalysisTimeframe,
    ) -> PatternAnalysisResult:
        request.validate()
        if request.as_of_date is None:
            raise ValueError("snapshot analysis requires an as-of date")
        return cast(PatternAnalysisResult, self._delegate.build_review_snapshot(
            request.symbol,
            timeframe,
            request.horizons,
            as_of_date=request.as_of_date,
            config_version=request.config_version,
            pivot_config=request.pivot_config,
        ))
