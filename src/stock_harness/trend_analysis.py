"""Explicit user-triggered trend analysis coordination."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
import hashlib
import json
import logging
import threading
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
    ClaimedAnalysisTarget,
    GeneratedAnalysisItem,
    GeneratedAnalysisTarget,
    GeneratedItemType,
)
from stock_harness.classic_patterns import detect_double_patterns
from stock_harness.consolidation_patterns import (
    BoundaryLine,
    detect_consolidation_patterns,
)
from stock_harness.diamond_patterns import detect_diamond_patterns
from stock_harness.breakout_state import (
    BreakoutDirection,
    StructuralEvent,
    StructuralEventKind,
    evaluate_breakout,
    evaluate_latest_boundary_event,
)
from stock_harness.key_levels import (
    detect_horizontal_levels,
    estimate_clear_space,
    estimate_daily_volume_profile,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.trend_pivots import (
    DirectionalChangeConfig,
    detect_directional_change_pivots,
)
from stock_harness.trend_lines_analysis import (
    TrendHorizon,
    generate_trend_line_candidates,
)


ALGORITHM_VERSION = "trend-diamond-patterns-v9"
LOGGER = logging.getLogger(__name__)


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
                settings={
                    "short_horizon_bars": horizons.short,
                    "medium_horizon_bars": horizons.medium,
                    "long_horizon_bars": horizons.long,
                    "include_preview": include_preview,
                    "atr_period": pivot_config.atr_period,
                    "atr_multiplier": pivot_config.atr_multiplier,
                    "minimum_reversal_percent": pivot_config.minimum_reversal_percent,
                },
            )
        )
        self._store.queue_generated_analysis_target(
            normalized, "trend", timeframe.value, cutoff, cutoff,
            "explicit-user-recalculate",
        )
        claim = self._store.claim_generated_analysis_target(target_id)
        if claim is None:
            raise RuntimeError("analysis target is already being calculated")
        return self._execute_claim(
            claim, horizons, cutoff, include_preview, pivot_config
        )

    def process_claim(self, claim: ClaimedAnalysisTarget) -> dict[str, object]:
        settings = claim.settings
        horizons = AnalysisHorizons(
            int(settings.get("short_horizon_bars", 60)),
            int(settings.get("medium_horizon_bars", 120)),
            int(settings.get("long_horizon_bars", 250)),
        )
        pivot_config = DirectionalChangeConfig(
            atr_period=int(settings.get("atr_period", 14)),
            atr_multiplier=float(settings.get("atr_multiplier", 2.0)),
            minimum_reversal_percent=float(
                settings.get("minimum_reversal_percent", 0.03)
            ),
        )
        latest = self._store.get_latest_daily_bar_date(claim.symbol)
        if latest is None:
            latest = claim.dirty_through
        return self._execute_claim(
            claim,
            horizons,
            latest,
            bool(settings.get("include_preview", False)),
            pivot_config,
        )

    def _execute_claim(
        self,
        claim: ClaimedAnalysisTarget,
        horizons: AnalysisHorizons,
        cutoff: date,
        include_preview: bool,
        pivot_config: DirectionalChangeConfig,
    ) -> dict[str, object]:
        normalized = claim.symbol
        timeframe = AnalysisTimeframe(claim.timeframe)
        target_id = claim.target_id
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
                algorithm_version=claim.algorithm_version,
                config_version=claim.config_version,
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
                items = _generated_items(
                    analysis_input, horizons, pivot_config
                )
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


class TrendAnalysisWorker:
    def __init__(
        self,
        store: SQLiteMarketDataStore,
        *,
        poll_interval_seconds: float = 2.0,
        batch_size: int = 5,
    ) -> None:
        self._store = store
        self._service = TrendAnalysisService(store)
        self._poll_interval_seconds = poll_interval_seconds
        self._batch_size = batch_size
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="stock-harness-trend-analysis", daemon=True
        )
        self._thread.start()
        LOGGER.info("trend_analysis_worker_started batch_size=%s", self._batch_size)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        LOGGER.info("trend_analysis_worker_stopped")

    def wake(self) -> None:
        self._wake.set()

    def run_once(self) -> int:
        pruned = self._store.prune_generated_analysis_runs(
            int(time.time() * 1000)
        )
        if pruned:
            LOGGER.info("trend_analysis_retention_pruned runs=%s", pruned)
        claims = self._store.claim_generated_analysis_targets(limit=self._batch_size)
        for claim in claims:
            try:
                self._service.process_claim(claim)
                LOGGER.info(
                    "trend_analysis_worker_completed symbol=%s timeframe=%s generation=%s",
                    claim.symbol, claim.timeframe, claim.generation,
                )
            except Exception:
                LOGGER.exception(
                    "trend_analysis_worker_failed symbol=%s timeframe=%s generation=%s",
                    claim.symbol, claim.timeframe, claim.generation,
                )
        return len(claims)

    def _run(self) -> None:
        while not self._stop.is_set():
            processed = self.run_once()
            if processed >= self._batch_size:
                continue
            self._wake.wait(self._poll_interval_seconds)
            self._wake.clear()


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


def _generated_items(
    analysis_input: AnalysisInput,
    horizons: AnalysisHorizons,
    base_config: DirectionalChangeConfig,
) -> list[GeneratedAnalysisItem]:
    profiles = (
        (
            TrendHorizon.SHORT,
            analysis_input.bars[-horizons.short:],
            base_config,
        ),
        (
            TrendHorizon.LONG,
            analysis_input.bars[-horizons.long:],
            DirectionalChangeConfig(
                atr_period=max(20, base_config.atr_period),
                atr_multiplier=min(10, base_config.atr_multiplier * 1.5),
                minimum_reversal_percent=min(
                    0.25, base_config.minimum_reversal_percent * 1.75
                ),
            ),
        ),
    )
    items: list[GeneratedAnalysisItem] = []
    long_bars = analysis_input.bars[-horizons.long:]
    long_pivots = ()
    for horizon, bars, config in profiles:
        pivots = detect_directional_change_pivots(bars, config)
        if horizon is TrendHorizon.LONG:
            long_pivots = pivots
        for index, pivot in enumerate(pivots):
            items.append(GeneratedAnalysisItem(
                item_id=f"{horizon.value}-pivot-{index}",
                item_type=GeneratedItemType.ANCHOR,
                payload={
                    "kind": pivot.kind.value,
                    "horizon": horizon.value,
                    "pivot_date": pivot.pivot_date.isoformat(),
                    "price": pivot.price,
                    "confirmed_date": (
                        pivot.confirmed_date.isoformat()
                        if pivot.confirmed_date else None
                    ),
                    "threshold": pivot.threshold,
                    "tentative": pivot.tentative,
                },
            ))
        lines = generate_trend_line_candidates(bars, pivots, horizon)
        for index, line in enumerate(lines):
            line_item_id = f"{horizon.value}-{line.kind.value}-line-{index}"
            items.append(GeneratedAnalysisItem(
                item_id=line_item_id,
                item_type=GeneratedItemType.LINE,
                payload={
                    "kind": line.kind.value,
                    "horizon": line.horizon.value,
                    "first_pivot_date": line.first_pivot_date.isoformat(),
                    "first_price": line.first_price,
                    "first_confirmed_date": line.first_confirmed_date.isoformat(),
                    "second_pivot_date": line.second_pivot_date.isoformat(),
                    "second_price": line.second_price,
                    "second_confirmed_date": line.second_confirmed_date.isoformat(),
                    "available_date": line.available_date.isoformat(),
                    "slope_per_bar": line.slope_per_bar,
                    "projected_price": line.projected_price,
                    "touch_count": line.touch_count,
                    "penetration_count": line.penetration_count,
                    "body_cross_count": line.body_cross_count,
                    "evaluated_bar_count": line.evaluated_bar_count,
                    "score": line.score,
                    "score_components": line.score_components,
                    "invalidation_reason": line.invalidation_reason,
                },
            ))
            event = evaluate_latest_boundary_event(
                bars,
                direction=(
                    BreakoutDirection.DOWN
                    if line.kind.value == "support" else BreakoutDirection.UP
                ),
                boundary_price=line.projected_price,
                previous_boundary_price=line.projected_price - line.slope_per_bar,
                preview=analysis_input.provisional_date is not None,
            )
            if event is not None:
                items.extend(_structural_event_items(line_item_id, event))
    profile = estimate_daily_volume_profile(long_bars)
    levels = detect_horizontal_levels(
        long_bars, long_pivots, volume_zones=profile.zones
    )
    for index, level in enumerate(levels):
        level_item_id = f"key-level-{index}"
        items.append(GeneratedAnalysisItem(
            item_id=level_item_id,
            item_type=GeneratedItemType.ZONE,
            payload={
                "kind": "key-level",
                "lower": level.lower,
                "upper": level.upper,
                "center": level.center,
                "observation_count": level.observation_count,
                "sources": level.sources,
                "evidence_dates": [item.isoformat() for item in level.evidence_dates],
                "latest_date": level.latest_date.isoformat(),
                "score": level.score,
                "score_components": level.score_components,
                "role_reversal": level.role_reversal,
                "volume_confluence": level.volume_confluence,
                "method": level.method,
                "uncertainty": "volatility-normalized historical price cluster",
            },
        ))
        if index < 3 and len(long_bars) >= 2:
            monitor_up = long_bars[-2].close <= level.center
            event = evaluate_latest_boundary_event(
                long_bars,
                direction=(
                    BreakoutDirection.UP if monitor_up else BreakoutDirection.DOWN
                ),
                boundary_price=level.upper if monitor_up else level.lower,
                preview=analysis_input.provisional_date is not None,
            )
            if event is not None:
                items.extend(_structural_event_items(level_item_id, event))
    for index, zone in enumerate(profile.zones):
        items.append(GeneratedAnalysisItem(
            item_id=f"volume-zone-{index}",
            item_type=GeneratedItemType.ZONE,
            payload={
                "kind": "estimated-volume-at-price",
                "lower": zone.lower,
                "upper": zone.upper,
                "center": zone.center,
                "estimated_volume": zone.estimated_volume,
                "estimated_share": zone.estimated_share,
                "evidence_dates": [item.isoformat() for item in zone.evidence_dates],
                "score": zone.score,
                "method": zone.method,
                "uncertainty": zone.uncertainty,
            },
        ))
    items.append(GeneratedAnalysisItem(
        item_id="key-level-volume-profile-evidence",
        item_type=GeneratedItemType.EVIDENCE,
        payload={
            "kind": "key-level-volume-profile-summary",
            "latest_close": long_bars[-1].close,
            "clear_space": estimate_clear_space(
                long_bars[-1].close, levels, profile.zones
            ),
            "volume_profile_available": bool(profile.zones),
            "total_estimated_volume": profile.total_estimated_volume,
            "bin_width": profile.bin_width,
            "method": profile.method,
            "uncertainty": (
                "daily volume is distributed uniformly across each bar high-low range; "
                "this is not exact position cost or main-force cost"
            ),
        },
    ))
    patterns = detect_double_patterns(long_bars, long_pivots)
    for index, pattern in enumerate(patterns):
        pattern_item_id = f"pattern-{pattern.pattern_type.value}-{index}"
        items.append(GeneratedAnalysisItem(
            item_id=pattern_item_id,
            item_type=GeneratedItemType.PATTERN,
            payload={
                "pattern_type": pattern.pattern_type.value,
                "display_name": pattern.display_name,
                "direction": pattern.direction.value,
                "timeframe": analysis_input.timeframe.value,
                "start_date": pattern.start_date.isoformat(),
                "end_date": pattern.end_date.isoformat(),
                "available_date": pattern.available_date.isoformat(),
                "pivots": [
                    {
                        "kind": pivot.kind,
                        "pivot_date": pivot.pivot_date.isoformat(),
                        "price": pivot.price,
                        "confirmed_date": pivot.confirmed_date.isoformat(),
                    }
                    for pivot in pattern.pivots
                ],
                "boundary_geometry": {
                    "kind": "horizontal-neckline",
                    "price": pattern.neckline_price,
                    "start_date": pattern.start_date.isoformat(),
                    "end_date": analysis_input.bars[-1].period_end.isoformat(),
                },
                "neckline_price": pattern.neckline_price,
                "completion_state": pattern.state.value,
                "breakout_date": (
                    pattern.breakout_date.isoformat()
                    if pattern.breakout_date else None
                ),
                "invalidation_price": pattern.invalidation_price,
                "invalidation_date": (
                    pattern.invalidation_date.isoformat()
                    if pattern.invalidation_date else None
                ),
                "score": pattern.score,
                "score_components": pattern.score_components,
                "volume_ratio": pattern.volume_ratio,
                "primary": pattern.primary,
            },
        ))
        evaluation = evaluate_breakout(
            long_bars,
            direction=(
                BreakoutDirection.UP
                if pattern.direction.value == "bullish"
                else BreakoutDirection.DOWN
            ),
            boundary_price=pattern.neckline_price,
            invalidation_price=pattern.invalidation_price,
            available_date=pattern.available_date,
            preview=analysis_input.provisional_date is not None,
        )
        for transition_index, transition in enumerate(evaluation.transitions):
            evidence = asdict(transition.evidence)
            evidence["trade_date"] = transition.evidence.trade_date.isoformat()
            items.append(GeneratedAnalysisItem(
                item_id=f"{pattern_item_id}-transition-{transition_index}",
                item_type=GeneratedItemType.TRANSITION,
                parent_item_id=pattern_item_id,
                payload={
                    "state": transition.state.value,
                    "transition_date": transition.transition_date.isoformat(),
                    "reason": transition.reason,
                    "preview": evaluation.preview,
                    "evidence": evidence,
                },
            ))
        items.append(GeneratedAnalysisItem(
            item_id=f"{pattern_item_id}-breakout-evidence",
            item_type=GeneratedItemType.EVIDENCE,
            parent_item_id=pattern_item_id,
            payload={
                "kind": "breakout-state-summary",
                "current_state": evaluation.current_state.value,
                "direction": evaluation.direction.value,
                "boundary_price": evaluation.boundary_price,
                "trigger_level": evaluation.boundary_price,
                "confirmation_level": evaluation.boundary_price,
                "failure_level": evaluation.boundary_price,
                "invalidation_level": evaluation.invalidation_price,
                "available_date": evaluation.available_date.isoformat(),
                "trigger_date": (
                    evaluation.trigger_date.isoformat()
                    if evaluation.trigger_date else None
                ),
                "confirmation_date": (
                    evaluation.confirmation_date.isoformat()
                    if evaluation.confirmation_date else None
                ),
                "failure_date": (
                    evaluation.failure_date.isoformat()
                    if evaluation.failure_date else None
                ),
                "preview": evaluation.preview,
                "analytical_only": True,
            },
        ))
    consolidations = detect_consolidation_patterns(long_bars, long_pivots)
    long_index_by_date = {
        bar.period_end: index for index, bar in enumerate(long_bars)
    }
    for index, pattern in enumerate(consolidations):
        pattern_item_id = f"pattern-{pattern.pattern_type.value}-{index}"
        latest_index = len(long_bars) - 1
        previous_index = latest_index - 1
        start_index = long_index_by_date[pattern.start_date]
        upper_latest = pattern.upper_boundary.start_price + pattern.upper_boundary.slope_per_bar * (latest_index - start_index)
        upper_previous = pattern.upper_boundary.start_price + pattern.upper_boundary.slope_per_bar * (previous_index - start_index)
        lower_latest = pattern.lower_boundary.start_price + pattern.lower_boundary.slope_per_bar * (latest_index - start_index)
        lower_previous = pattern.lower_boundary.start_price + pattern.lower_boundary.slope_per_bar * (previous_index - start_index)
        monitor_up = long_bars[-2].close <= (upper_previous + lower_previous) / 2
        boundary_latest = upper_latest if monitor_up else lower_latest
        boundary_previous = upper_previous if monitor_up else lower_previous
        items.append(GeneratedAnalysisItem(
            item_id=pattern_item_id,
            item_type=GeneratedItemType.PATTERN,
            payload={
                "pattern_type": pattern.pattern_type.value,
                "display_name": pattern.display_name,
                "direction": pattern.breakout_direction or "neutral",
                "timeframe": analysis_input.timeframe.value,
                "start_date": pattern.start_date.isoformat(),
                "end_date": pattern.end_date.isoformat(),
                "available_date": pattern.available_date.isoformat(),
                "pivots": [
                    {
                        "kind": pivot.kind.value,
                        "pivot_date": pivot.pivot_date.isoformat(),
                        "price": pivot.price,
                        "confirmed_date": pivot.confirmed_date.isoformat(),
                    }
                    for pivot in pattern.pivots
                ],
                "boundary_geometry": {
                    "kind": "two-lines",
                    "upper": _boundary_payload(pattern.upper_boundary),
                    "lower": _boundary_payload(pattern.lower_boundary),
                },
                "neckline_price": boundary_latest,
                "completion_state": pattern.completion_state,
                "breakout_date": (
                    pattern.breakout_date.isoformat()
                    if pattern.breakout_date else None
                ),
                "invalidation_price": (
                    lower_latest if monitor_up else upper_latest
                ),
                "invalidation_date": (
                    pattern.invalidation_date.isoformat()
                    if pattern.invalidation_date else None
                ),
                "score": pattern.score,
                "score_components": pattern.score_components,
                "volume_ratio": pattern.volume_ratio,
                "primary": pattern.primary,
            },
        ))
        event = evaluate_latest_boundary_event(
            long_bars,
            direction=(BreakoutDirection.UP if monitor_up else BreakoutDirection.DOWN),
            boundary_price=boundary_latest,
            previous_boundary_price=boundary_previous,
            preview=analysis_input.provisional_date is not None,
        )
        if event is not None:
            items.extend(_structural_event_items(pattern_item_id, event))
    diamonds = detect_diamond_patterns(long_bars, long_pivots)
    for index, pattern in enumerate(diamonds):
        pattern_item_id = f"pattern-{pattern.pattern_type.value}-{index}"
        latest_index = len(long_bars) - 1
        previous_index = latest_index - 1
        upper_start_index = long_index_by_date[pattern.upper_active.start_date]
        lower_start_index = long_index_by_date[pattern.lower_active.start_date]
        upper_latest = pattern.upper_active.start_price + pattern.upper_active.slope_per_bar * (latest_index - upper_start_index)
        upper_previous = pattern.upper_active.start_price + pattern.upper_active.slope_per_bar * (previous_index - upper_start_index)
        lower_latest = pattern.lower_active.start_price + pattern.lower_active.slope_per_bar * (latest_index - lower_start_index)
        lower_previous = pattern.lower_active.start_price + pattern.lower_active.slope_per_bar * (previous_index - lower_start_index)
        monitor_up = long_bars[-2].close <= (upper_previous + lower_previous) / 2
        boundary_latest = upper_latest if monitor_up else lower_latest
        boundary_previous = upper_previous if monitor_up else lower_previous
        items.append(GeneratedAnalysisItem(
            item_id=pattern_item_id,
            item_type=GeneratedItemType.PATTERN,
            payload={
                "pattern_type": pattern.pattern_type.value,
                "display_name": pattern.display_name,
                "direction": pattern.breakout_direction or "neutral",
                "timeframe": analysis_input.timeframe.value,
                "start_date": pattern.start_date.isoformat(),
                "end_date": pattern.end_date.isoformat(),
                "available_date": pattern.available_date.isoformat(),
                "pivots": [
                    {
                        "kind": pivot.kind.value,
                        "pivot_date": pivot.pivot_date.isoformat(),
                        "price": pivot.price,
                        "confirmed_date": pivot.confirmed_date.isoformat(),
                    }
                    for pivot in pattern.pivots
                ],
                "boundary_geometry": {
                    "kind": "broadening-then-contracting",
                    "segments": [
                        _boundary_payload(value)
                        for value in pattern.boundary_segments
                    ],
                },
                "neckline_price": boundary_latest,
                "completion_state": pattern.completion_state,
                "breakout_date": (
                    pattern.breakout_date.isoformat()
                    if pattern.breakout_date else None
                ),
                "invalidation_price": lower_latest if monitor_up else upper_latest,
                "invalidation_date": (
                    pattern.invalidation_date.isoformat()
                    if pattern.invalidation_date else None
                ),
                "score": pattern.score,
                "score_components": pattern.score_components,
                "context_change_percent": pattern.context_change_percent,
                "volume_ratio": pattern.volume_ratio,
                "primary": pattern.primary,
            },
        ))
        event = evaluate_latest_boundary_event(
            long_bars,
            direction=(BreakoutDirection.UP if monitor_up else BreakoutDirection.DOWN),
            boundary_price=boundary_latest,
            previous_boundary_price=boundary_previous,
            preview=analysis_input.provisional_date is not None,
        )
        if event is not None:
            items.extend(_structural_event_items(pattern_item_id, event))
    return items


def _boundary_payload(value: BoundaryLine) -> dict[str, object]:
    return {
        "start_date": value.start_date.isoformat(),
        "start_price": value.start_price,
        "end_date": value.end_date.isoformat(),
        "end_price": value.end_price,
        "slope_per_bar": value.slope_per_bar,
    }


def _structural_event_items(
    parent_item_id: str,
    event: StructuralEvent,
) -> list[GeneratedAnalysisItem]:
    state = {
        StructuralEventKind.UPWARD_BREAKOUT: "triggered",
        StructuralEventKind.DOWNWARD_BREAKDOWN: "triggered",
        StructuralEventKind.RETEST: "retesting",
        StructuralEventKind.FALSE_BREAKOUT_RISK: "failed",
        StructuralEventKind.NO_CHANGE: "ready",
    }[event.kind]
    evidence = asdict(event.evidence)
    evidence["trade_date"] = event.evidence.trade_date.isoformat()
    payload = {
        "event_kind": event.kind.value,
        "current_state": state,
        "direction": event.direction.value,
        "event_date": event.event_date.isoformat(),
        "boundary_price": event.boundary_price,
        "previous_boundary_price": event.previous_boundary_price,
        "preview": event.preview,
        "reason": event.reason,
        "evidence": evidence,
    }
    return [
        GeneratedAnalysisItem(
            item_id=f"{parent_item_id}-latest-event-transition",
            item_type=GeneratedItemType.TRANSITION,
            parent_item_id=parent_item_id,
            payload=payload,
        ),
        GeneratedAnalysisItem(
            item_id=f"{parent_item_id}-latest-event-evidence",
            item_type=GeneratedItemType.EVIDENCE,
            parent_item_id=parent_item_id,
            payload={
                "kind": "latest-structural-event-summary",
                **payload,
                "invalidation_level": event.boundary_price,
                "trigger_date": (
                    event.event_date.isoformat()
                    if event.kind in {
                        StructuralEventKind.UPWARD_BREAKOUT,
                        StructuralEventKind.DOWNWARD_BREAKDOWN,
                    } else None
                ),
                "failure_date": (
                    event.event_date.isoformat()
                    if event.kind is StructuralEventKind.FALSE_BREAKOUT_RISK
                    else None
                ),
            },
        ),
    ]


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
