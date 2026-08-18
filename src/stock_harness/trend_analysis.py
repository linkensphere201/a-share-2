"""Explicit user-triggered trend analysis coordination."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
import hashlib
import json
import time
from typing import Sequence

from stock_harness.analysis_inputs import (
    AnalysisHorizons,
    AnalysisInput,
    AnalysisInputMode,
    AnalysisInputService,
    AnalysisTimeframe,
)
from stock_harness.analysis_results import (
    AnalysisNamespace,
    AnalysisRunSpec,
    GeneratedAnalysisItem,
    GeneratedAnalysisTarget,
    GeneratedItemType,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.trend_pivots import (
    DirectionalChangeConfig,
    detect_directional_change_pivots,
)


ALGORITHM_VERSION = "directional-change-pivots-v1"


class TrendAnalysisService:
    def __init__(self, store: SQLiteMarketDataStore) -> None:
        self._store = store
        self._inputs = AnalysisInputService(store)

    def recalculate(
        self,
        symbol: str,
        timeframes: Sequence[AnalysisTimeframe],
        horizons: AnalysisHorizons,
        *,
        config_version: str,
        include_preview: bool,
        as_of_date: date | None = None,
        pivot_config: DirectionalChangeConfig = DirectionalChangeConfig(),
    ) -> list[dict[str, object]]:
        cutoff = as_of_date or date.today()
        results: list[dict[str, object]] = []
        for timeframe in dict.fromkeys(timeframes):
            results.append(self._recalculate_one(
                symbol, timeframe, horizons, cutoff, config_version,
                include_preview, pivot_config,
            ))
        return results

    def _recalculate_one(
        self,
        symbol: str,
        timeframe: AnalysisTimeframe,
        horizons: AnalysisHorizons,
        cutoff: date,
        config_version: str,
        include_preview: bool,
        pivot_config: DirectionalChangeConfig,
    ) -> dict[str, object]:
        normalized = symbol.strip().upper()
        target_id = self._store.upsert_generated_analysis_target(
            GeneratedAnalysisTarget(
                normalized, "trend", timeframe.value,
                ALGORITHM_VERSION, config_version,
            )
        )
        self._store.queue_generated_analysis_target(
            normalized, "trend", timeframe.value, cutoff, cutoff,
            "explicit-user-recalculate",
        )
        claim = self._store.claim_generated_analysis_target(target_id)
        if claim is None:
            raise RuntimeError("analysis target is already being calculated")
        started = time.perf_counter()
        run_id: str | None = None
        try:
            analysis_input = self._inputs.build(
                normalized,
                cutoff,
                timeframe,
                AnalysisInputMode.PREVIEW if include_preview else AnalysisInputMode.FINAL,
                horizons,
            )
            if not analysis_input.bars:
                raise ValueError(f"no analysis bars available for {normalized}")
            namespace = (
                AnalysisNamespace.PREVIEW
                if analysis_input.provisional_date is not None
                else AnalysisNamespace.OFFICIAL
            )
            spec = AnalysisRunSpec(
                system_id="trend", symbol=normalized, timeframe=timeframe.value,
                namespace=namespace, as_of_date=cutoff,
                input_start_date=analysis_input.bars[0].period_start,
                input_end_date=analysis_input.bars[-1].period_end,
                input_digest=_input_digest(analysis_input),
                algorithm_version=ALGORITHM_VERSION,
                config_version=config_version,
                completion_state=(
                    "complete" if analysis_input.bars[-1].period_complete else "partial"
                ),
                source_observed_at_ms=(
                    _datetime_ms(analysis_input.provisional_provider_time)
                    if analysis_input.provisional_provider_time is not None else None
                ),
                expires_at_ms=(
                    _datetime_ms(analysis_input.provisional_provider_time + timedelta(hours=48))
                    if analysis_input.provisional_provider_time is not None else None
                ),
            )
            run = self._store.begin_generated_analysis_run(spec)
            run_id = run.run_id
            if not run.reused:
                pivots = detect_directional_change_pivots(
                    analysis_input.bars, pivot_config
                )
                items = [
                    GeneratedAnalysisItem(
                        item_id=f"pivot-{index}",
                        item_type=GeneratedItemType.ANCHOR,
                        payload={
                            "kind": pivot.kind.value,
                            "pivot_date": pivot.pivot_date.isoformat(),
                            "price": pivot.price,
                            "confirmed_date": (
                                pivot.confirmed_date.isoformat()
                                if pivot.confirmed_date else None
                            ),
                            "threshold": pivot.threshold,
                            "tentative": pivot.tentative,
                        },
                    )
                    for index, pivot in enumerate(pivots)
                ]
                self._store.complete_generated_analysis_run(
                    run.run_id,
                    items,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    warnings=[_warning_payload(item) for item in analysis_input.warnings],
                )
            self._store.complete_generated_analysis_target(
                target_id, claim.generation
            )
            latest = self._store.get_latest_generated_analysis_run(
                normalized, "trend", timeframe.value, namespace
            )
            if latest is None:
                raise RuntimeError("completed analysis result is not readable")
            return _public_result(latest)
        except Exception as error:
            duration_ms = (time.perf_counter() - started) * 1000
            if run_id is not None:
                try:
                    self._store.fail_generated_analysis_run(
                        run_id, str(error), duration_ms=duration_ms
                    )
                except ValueError:
                    pass
            self._store.fail_generated_analysis_target(
                target_id, claim.generation, str(error)
            )
            raise


def _input_digest(value: AnalysisInput) -> bytes:
    payload = {
        "symbol": value.symbol,
        "timeframe": value.timeframe.value,
        "mode": value.mode.value,
        "as_of_date": value.as_of_date.isoformat(),
        "price_basis": value.price_basis,
        "bars": [
            {
                "start": bar.period_start.isoformat(),
                "end": bar.period_end.isoformat(),
                "open": bar.open, "high": bar.high, "low": bar.low,
                "close": bar.close, "volume": bar.volume,
                "sources": bar.sources, "provisional": bar.contains_provisional,
                "complete": bar.period_complete, "observed_at_ms": bar.observed_at_ms,
            }
            for bar in value.bars
        ],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).digest()


def _datetime_ms(value) -> int:
    return int(value.timestamp() * 1000)


def _warning_payload(value) -> dict[str, object]:
    payload = asdict(value)
    payload["dates"] = [item.isoformat() for item in value.dates]
    return payload


def _public_result(value: dict[str, object]) -> dict[str, object]:
    result = dict(value)
    digest = result.get("input_digest")
    if isinstance(digest, bytes):
        result["input_digest"] = digest.hex()
    return result
