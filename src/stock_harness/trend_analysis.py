"""Explicit user-triggered trend analysis coordination."""

from __future__ import annotations

from dataclasses import asdict, replace
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
from stock_harness.analysis_projection import build_core_projection_item
from stock_harness.breakout_state import (
    BreakoutDirection,
    StructuralEvent,
    StructuralEventKind,
    evaluate_latest_boundary_event,
)
from stock_harness.generated_patterns import generate_pattern_items
from stock_harness.key_levels import (
    detect_horizontal_levels,
    estimate_clear_space,
    estimate_daily_volume_profile,
)
from stock_harness.major_descending_lines import detect_major_descending_lines
from stock_harness.pattern_ranking import rank_pattern_candidates
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.trend_pivots import (
    DirectionalChangeConfig,
    detect_directional_change_pivots,
)
from stock_harness.trend_lines_analysis import (
    TrendHorizon,
    generate_trend_line_candidates,
)
from stock_harness.trend_context import (
    TrendContextSeries,
    build_trend_context_evidence,
)


ALGORITHM_VERSION = "trend-causal-replay-v25"
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

    def build_review_snapshot(
        self,
        symbol: str,
        timeframe: AnalysisTimeframe,
        horizons: AnalysisHorizons,
        *,
        as_of_date: date,
        config_version: str,
        pivot_config: DirectionalChangeConfig = DirectionalChangeConfig(),
    ) -> dict[str, object]:
        """Calculate an isolated final-only snapshot without registering a target."""
        started = time.perf_counter()
        normalized = symbol.strip().upper()
        analysis_input = self._inputs.build(
            normalized, as_of_date, timeframe, AnalysisInputMode.FINAL, horizons
        )
        if not analysis_input.bars:
            raise ValueError(f"no analysis bars available for {normalized}")
        detector_input, roll_qualification = _qualify_roll_input(analysis_input)
        context_payload = self._build_context_evidence(
            detector_input, as_of_date, timeframe, horizons
        )
        digest = _input_digest(analysis_input, context_payload)
        generated = _generated_items(detector_input, horizons, pivot_config)
        generated.append(GeneratedAnalysisItem(
            item_id="market-board-context-evidence",
            item_type=GeneratedItemType.EVIDENCE,
            payload=context_payload,
        ))
        generated = _apply_roll_qualification(generated, roll_qualification)
        return {
            "run_id": f"review-{digest.hex()[:24]}",
            "status": "succeeded",
            "as_of_date": as_of_date,
            "input_start_date": analysis_input.bars[0].period_start,
            "input_end_date": analysis_input.bars[-1].period_end,
            "input_digest": digest.hex(),
            "algorithm_version": ALGORITHM_VERSION,
            "config_version": config_version,
            "completion_state": (
                "complete" if analysis_input.bars[-1].period_complete else "partial"
            ),
            "attempt": 1,
            "duration_ms": (time.perf_counter() - started) * 1000,
            "warnings": [_warning_payload(item) for item in analysis_input.warnings],
            "failure_details": None,
            "source_observed_at_ms": None,
            "expires_at_ms": None,
            "supersedes_run_id": None,
            "created_at_ms": None,
            "completed_at_ms": None,
            "stale": False,
            "stale_reasons": [],
            "items": [
                {
                    "item_id": item.item_id,
                    "item_type": item.item_type.value,
                    "parent_item_id": item.parent_item_id,
                    "payload": item.payload,
                }
                for item in generated
            ],
        }

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
            detector_input, roll_qualification = _qualify_roll_input(analysis_input)
            context_payload = self._build_context_evidence(
                detector_input, cutoff, timeframe, horizons
            )
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
                input_digest=_input_digest(analysis_input, context_payload),
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
                    detector_input, horizons, pivot_config
                )
                items.append(GeneratedAnalysisItem(
                    item_id="market-board-context-evidence",
                    item_type=GeneratedItemType.EVIDENCE,
                    payload=context_payload,
                ))
                items = _apply_roll_qualification(items, roll_qualification)
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

    def _build_context_evidence(
        self,
        subject_input: AnalysisInput,
        cutoff: date,
        timeframe: AnalysisTimeframe,
        horizons: AnalysisHorizons,
    ) -> dict[str, object]:
        summary = self._store.get_instrument_summary(subject_input.symbol) or {}
        subject = TrendContextSeries(
            subject_input.symbol,
            str(summary.get("name", subject_input.symbol)),
            "subject",
            subject_input.bars,
        )
        unavailable: list[dict[str, str]] = []
        market: TrendContextSeries | None = None
        benchmark = _market_benchmark(subject_input.symbol)
        if benchmark is not None and benchmark != subject_input.symbol:
            market = self._load_context_series(
                benchmark, "market", cutoff, timeframe, horizons, unavailable
            )
        related: list[TrendContextSeries] = []
        try:
            mappings = self._store.list_symbol_analysis_contexts(
                subject_input.symbol, limit=8
            )
        except Exception as error:
            mappings = []
            unavailable.append({
                "symbol": subject_input.symbol,
                "reason": f"context mapping unavailable: {error}",
            })
        for mapping in mappings:
            symbol = str(mapping["symbol"])
            series = self._load_context_series(
                symbol,
                str(mapping.get("kind", "board")),
                cutoff,
                timeframe,
                horizons,
                unavailable,
                name=str(mapping.get("name", symbol)),
            )
            if series is not None:
                related.append(series)
            if len(related) >= 5:
                break
        return build_trend_context_evidence(
            subject, market, related, unavailable=unavailable
        )

    def _load_context_series(
        self,
        symbol: str,
        kind: str,
        cutoff: date,
        timeframe: AnalysisTimeframe,
        horizons: AnalysisHorizons,
        unavailable: list[dict[str, str]],
        *,
        name: str | None = None,
    ) -> TrendContextSeries | None:
        try:
            value = self._inputs.build(
                symbol, cutoff, timeframe, AnalysisInputMode.FINAL, horizons
            )
        except Exception as error:
            unavailable.append({"symbol": symbol, "reason": str(error)})
            return None
        if len(value.bars) < 2:
            unavailable.append({
                "symbol": symbol,
                "reason": "fewer than two visible context bars",
            })
            return None
        summary = self._store.get_instrument_summary(symbol) or {}
        return TrendContextSeries(
            symbol,
            name or str(summary.get("name", symbol)),
            kind,
            value.bars,
        )


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


def _qualify_roll_input(
    value: AnalysisInput,
) -> tuple[AnalysisInput, dict[str, object] | None]:
    if value.instrument.kind != "futures-continuous":
        return value, None
    roll_indexes = [
        index for index, bar in enumerate(value.bars) if bar.contains_roll_event
    ]
    if not roll_indexes:
        return value, None

    latest_roll_index = roll_indexes[-1]
    raw_basis = value.price_basis == "raw"
    selected = value.bars[latest_roll_index:] if raw_basis else value.bars
    qualified_bars = tuple(
        replace(
            bar,
            volume=0,
            amount=None,
            open_interest_change=None,
        ) if bar.contains_roll_event else bar
        for bar in selected
    )
    qualification = {
        "kind": "futures-roll-qualification",
        "mode": (
            "post-latest-roll-segment" if raw_basis
            else "adjusted-series-qualified"
        ),
        "price_basis": value.price_basis,
        "roll_dates": [
            value.bars[index].period_end.isoformat() for index in roll_indexes
        ],
        "latest_roll_date": value.bars[latest_roll_index].period_end.isoformat(),
        "excluded_prefix_bars": latest_roll_index if raw_basis else 0,
        "eligible_start_date": qualified_bars[0].period_start.isoformat(),
        "eligible_end_date": qualified_bars[-1].period_end.isoformat(),
        "excluded_roll_volume_dates": [
            bar.period_end.isoformat() for bar in selected if bar.contains_roll_event
        ],
        "price_evidence_policy": (
            "Price evidence before the latest raw-series roll is excluded."
            if raw_basis else
            "Adjusted price history is retained and every result is roll-qualified."
        ),
        "flow_evidence_policy": (
            "Roll-period volume, amount, and open-interest change are excluded from scoring."
        ),
    }
    return replace(value, bars=qualified_bars), qualification


def _apply_roll_qualification(
    items: Sequence[GeneratedAnalysisItem],
    qualification: dict[str, object] | None,
) -> list[GeneratedAnalysisItem]:
    if qualification is None:
        return list(items)
    marker = {
        "mode": qualification["mode"],
        "price_basis": qualification["price_basis"],
        "roll_dates": qualification["roll_dates"],
    }
    qualified = [
        GeneratedAnalysisItem(
            item_id=item.item_id,
            item_type=item.item_type,
            payload={**item.payload, "roll_qualification": marker},
            parent_item_id=item.parent_item_id,
        )
        for item in items
    ]
    qualified.append(GeneratedAnalysisItem(
        item_id="futures-roll-qualification",
        item_type=GeneratedItemType.EVIDENCE,
        payload=qualification,
    ))
    return qualified


def _input_digest(
    value: AnalysisInput,
    context_payload: dict[str, object] | None = None,
) -> bytes:
    payload = {
        "symbol": value.symbol,
        "timeframe": value.timeframe.value,
        "mode": value.mode.value,
        "as_of_date": value.as_of_date.isoformat(),
        "price_basis": value.price_basis,
        "volume_semantics": value.volume_semantics,
        "instrument": asdict(value.instrument),
        "bars": [
            {
                "start": bar.period_start.isoformat(),
                "end": bar.period_end.isoformat(),
                "open": bar.open, "high": bar.high, "low": bar.low,
                "close": bar.close, "volume": bar.volume,
                "sources": bar.sources, "provisional": bar.contains_provisional,
                "complete": bar.period_complete,
                "observed_at_ms": (
                    bar.observed_at_ms if bar.contains_provisional else None
                ),
                "settlement": bar.settlement,
                "previous_settlement": bar.previous_settlement,
                "amount": bar.amount,
                "open_interest": bar.open_interest,
                "open_interest_change": bar.open_interest_change,
                "mapped_contracts": bar.mapped_contracts,
                "roll_event": bar.contains_roll_event,
            }
            for bar in value.bars
        ],
        "context": context_payload,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).digest()


def _market_benchmark(symbol: str) -> str | None:
    normalized = symbol.upper()
    code = normalized.split(".", 1)[0]
    if normalized.endswith(".SH"):
        return "000688.SH" if code.startswith("688") else "000001.SH"
    if normalized.endswith(".SZ"):
        return "399006.SZ" if code.startswith(("300", "301")) else "399001.SZ"
    if normalized.endswith(".BJ"):
        return "899050.BJ"
    return None


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
            TrendHorizon.MEDIUM,
            analysis_input.bars[-horizons.medium:],
            DirectionalChangeConfig(
                atr_period=max(16, base_config.atr_period),
                atr_multiplier=min(10, base_config.atr_multiplier * 1.25),
                minimum_reversal_percent=min(
                    0.25, base_config.minimum_reversal_percent * 1.35
                ),
            ),
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
    items: list[GeneratedAnalysisItem] = [GeneratedAnalysisItem(
        item_id="analysis-input-capabilities",
        item_type=GeneratedItemType.EVIDENCE,
        payload={
            "kind": "analysis-input-capabilities",
            "instrument": asdict(analysis_input.instrument),
            "price_basis": analysis_input.price_basis,
            "volume_semantics": analysis_input.volume_semantics,
            "input_mode": analysis_input.mode.value,
            "roll_event_periods": sum(
                item.contains_roll_event for item in analysis_input.bars
            ),
            "mapped_contracts": sorted({
                symbol
                for item in analysis_input.bars
                for symbol in item.mapped_contracts
            }),
        },
    )]
    for line in detect_major_descending_lines(analysis_input.bars):
        items.append(GeneratedAnalysisItem(
            item_id=line.item_id,
            item_type=GeneratedItemType.LINE,
            payload={
                "kind": "resistance",
                "horizon": "long",
                "major_line_code": line.code,
                "major_line_period": line.period.value,
                "major_line_state": line.state.value,
                "first_pivot_date": line.first_date,
                "first_price": line.first_price,
                "first_confirmed_date": line.first_date,
                "second_pivot_date": line.second_date,
                "second_price": line.second_price,
                "second_confirmed_date": line.second_date,
                "available_date": line.second_date,
                "slope_per_bar": line.slope_per_bar,
                "projected_price": line.projected_price,
                "distance_percent": line.distance_percent,
                "touch_count": line.touch_count,
                "independent_touch_count": line.independent_touch_count,
                "penetration_count": line.penetration_count,
                "wick_breach_count": line.wick_breach_count,
                "body_breach_count": line.body_breach_count,
                "close_breach_count": line.close_breach_count,
                "maximum_wick_breach_percent": line.maximum_wick_breach_percent,
                "maximum_body_breach_percent": line.maximum_body_breach_percent,
                "maximum_close_breach_percent": line.maximum_close_breach_percent,
                "maximum_breach_atr": line.maximum_breach_atr,
                "first_prominence_percent": line.first_prominence_percent,
                "second_prominence_percent": line.second_prominence_percent,
                "anchor_span_bars": line.anchor_span_bars,
                "decline_percent": line.decline_percent,
                "breakout_date": line.breakout_date,
                "volume_ratio_5": line.volume_ratio_5,
                "support_price": line.support_price,
                "invalidation_price": line.invalidation_price,
                "first_target_price": line.first_target_price,
                "major_target_price": line.major_target_price,
                "first_risk_reward": line.first_risk_reward,
                "major_risk_reward": line.major_risk_reward,
                "trade_scenario": line.trade_scenario,
                "score": line.score,
                "score_components": {"shared_major_line_detector": 1.0},
                "invalidation_reason": None,
            },
        ))
    long_bars = analysis_input.bars[-horizons.long:]
    long_pivots = ()
    pivots_by_horizon = {}
    for horizon, bars, config in profiles:
        pivots = detect_directional_change_pivots(bars, config)
        pivots_by_horizon[horizon] = pivots
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
                    "independent_touch_count": line.independent_touch_count,
                    "penetration_count": line.penetration_count,
                    "body_cross_count": line.body_cross_count,
                    "wick_breach_count": line.penetration_count,
                    "body_breach_count": line.body_cross_count,
                    "close_breach_count": line.close_breach_count,
                    "maximum_wick_breach_percent": line.maximum_wick_breach_percent,
                    "maximum_body_breach_percent": line.maximum_body_breach_percent,
                    "maximum_close_breach_percent": line.maximum_close_breach_percent,
                    "maximum_breach_atr": line.maximum_breach_atr,
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
    for horizon, bars, _config in profiles:
        items.extend(generate_pattern_items(
            bars, pivots_by_horizon[horizon], horizon,
            analysis_input.timeframe,
            preview=analysis_input.provisional_date is not None,
        ))
    ranked = rank_pattern_candidates(items)
    ranked.append(build_core_projection_item(ranked))
    return ranked

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
