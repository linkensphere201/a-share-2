"""Manual signal-review execution with immutable, comparable snapshots."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import date, timedelta
import hashlib
import json
import logging
import threading
import time
from uuid import uuid4

from stock_harness.board_leader_scan import (
    ALGORITHM_VERSION,
    HISTORICAL_PROFILE,
    RECENT_PROFILE,
    calculate_stock_features,
    compact_returns,
    is_risk_name,
    rank_board_leaders,
)
from stock_harness.board_hotspot_features import extract_board_hotspot_features
from stock_harness.board_capacity import classify_board_capacities
from stock_harness.market_liquidity import (
    analyze_benchmark_volume_fallback, analyze_market_liquidity,
)
from stock_harness.analysis_projection import read_core_structural_item_ids
from stock_harness.daily_signal_analysis import (
    CONFIG_VERSION as DAILY_CONFIG_VERSION,
    LOOKBACK_BARS as DAILY_LOOKBACK_BARS,
    analyze_daily_series,
    build_board_analysis_record,
    observation_digest,
    render_board_summary,
)
from stock_harness.models import InstrumentKind
from stock_harness.hotspot_wave import project_hotspot_waves
from stock_harness.observation_systems import (
    BOARD_HOTSPOT_SYSTEM,
    BoardHotspotSystem,
    ObservationSystemContext,
    ObservationSystemRegistry,
    TrendBreakoutSystem,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.review_scoring import (
    MARKET_REGIME_SCORER,
    RECOGNITION_SCORER,
    STOCK_OPPORTUNITY_SCORER,
    TREND_BREAKOUT_SCORER,
    default_scorer_registry,
    execute_scorer,
)
from stock_harness.structural_scenario_engine import project_scenario_summary
from stock_harness.stock_observation_scan import (
    build_board_member_scan_snapshot,
    build_unified_stock_pool_snapshot,
    scan_full_market_independent_strength,
)
from stock_harness.stock_observation_layers import (
    ALGORITHM_VERSION as STOCK_PRESENTATION_VERSION,
    FOCUS_LIMIT as STOCK_FOCUS_LIMIT,
    M4_ALLOCATOR_VERSION as STOCK_M4_ALLOCATOR_VERSION,
    RISK_LIMIT as STOCK_RISK_LIMIT,
    assign_stock_presentation_layers,
    select_stock_m4_candidates,
)


LOGGER = logging.getLogger(__name__)
WEEKLY_RECOGNITION_SIGNAL = "weekly-board-recognition"
DEFINITION_VERSION = "weekly-board-recognition-v1"
DAILY_MARKET_BOARD_SIGNAL = "daily-market-board-review"
DAILY_DEFINITION_VERSION = "daily-market-board-review-v1"
DAILY_REVIEW_ALGORITHM_VERSION = "daily-market-board-review-v4-systems-v2"
BOARD_POOL_VERSION = "board-observation-pool-v2"
STOCK_OBSERVATION_SIGNAL = "stock-observation-pool"
HISTORICAL_LIMIT = 5


def _board_observation_system_registry() -> ObservationSystemRegistry:
    scorers = default_scorer_registry()
    registry = ObservationSystemRegistry()
    registry.register(TrendBreakoutSystem(
        scorers.get(TREND_BREAKOUT_SCORER).version,
    ))
    registry.register(BoardHotspotSystem())
    return registry


class SignalReviewBusyError(RuntimeError):
    pass


class SignalReviewService:
    def __init__(self, store: SQLiteMarketDataStore) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        recovered = store.recover_interrupted_signal_review_runs()
        if recovered:
            LOGGER.warning("signal_review_interrupted_runs_recovered count=%s", recovered)

    @staticmethod
    def definitions() -> list[dict[str, object]]:
        return [{
            "signal_id": WEEKLY_RECOGNITION_SIGNAL,
            "name": "板块辨识度周观察",
            "description": "重算近期与历史高权辨识度品种，并与上一轮结果比较。",
            "cadence": "weekly",
            "definition_version": DEFINITION_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "manual_only": True,
            "profiles": [RECENT_PROFILE, HISTORICAL_PROFILE],
        }, {
            "signal_id": DAILY_MARKET_BOARD_SIGNAL,
            "name": "每日大盘与板块复盘",
            "description": "保存全板块一级固定分析，并筛选值得持续关注的异动。",
            "cadence": "daily",
            "definition_version": DAILY_DEFINITION_VERSION,
            "algorithm_version": DAILY_REVIEW_ALGORITHM_VERSION,
            "manual_only": True,
            "profiles": ["market", "attention"],
            "observation_systems": _board_observation_system_registry().definitions(),
        }]

    def start_run(
        self, signal_id: str, effective_date: date | None = None,
    ) -> dict[str, object]:
        if signal_id not in {WEEKLY_RECOGNITION_SIGNAL, DAILY_MARKET_BOARD_SIGNAL}:
            raise ValueError(f"unknown signal: {signal_id}")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise SignalReviewBusyError("another signal review run is already active")
            cutoff = effective_date or self._store.get_latest_stock_daily_bar_date()
            if cutoff is None:
                raise ValueError("no completed stock daily bars are available")
            daily = signal_id == DAILY_MARKET_BOARD_SIGNAL
            run = self._store.create_signal_review_run(
                signal_id=signal_id,
                definition_version=(DAILY_DEFINITION_VERSION if daily else DEFINITION_VERSION),
                algorithm_version=(
                    DAILY_REVIEW_ALGORITHM_VERSION if daily else ALGORITHM_VERSION
                ),
                cadence="daily" if daily else "weekly",
                effective_date=cutoff,
                parameters=_daily_run_parameters() if daily else _run_parameters(),
            )
            self._thread = threading.Thread(
                target=self._run_guarded,
                args=(str(run["run_id"]), cutoff, signal_id),
                name="stock-harness-signal-review", daemon=True,
            )
            self._thread.start()
            return run

    def run_sync(self, signal_id: str, effective_date: date) -> dict[str, object]:
        if signal_id not in {WEEKLY_RECOGNITION_SIGNAL, DAILY_MARKET_BOARD_SIGNAL}:
            raise ValueError(f"unknown signal: {signal_id}")
        daily = signal_id == DAILY_MARKET_BOARD_SIGNAL
        run = self._store.create_signal_review_run(
            signal_id=signal_id,
            definition_version=DAILY_DEFINITION_VERSION if daily else DEFINITION_VERSION,
            algorithm_version=(
                DAILY_REVIEW_ALGORITHM_VERSION if daily else ALGORITHM_VERSION
            ),
            cadence="daily" if daily else "weekly",
            effective_date=effective_date,
            parameters=_daily_run_parameters() if daily else _run_parameters(),
        )
        if daily:
            self._execute_daily(str(run["run_id"]), effective_date)
        else:
            self._execute(str(run["run_id"]), effective_date)
        return self._store.get_signal_review_run(str(run["run_id"]))  # type: ignore[return-value]

    def _run_guarded(self, run_id: str, cutoff: date, signal_id: str) -> None:
        try:
            if signal_id == DAILY_MARKET_BOARD_SIGNAL:
                self._execute_daily(run_id, cutoff)
            else:
                self._execute(run_id, cutoff)
        except Exception as error:
            self._store.fail_signal_review_run(run_id, str(error))
            LOGGER.exception("signal_review_run_failed run_id=%s", run_id)

    def _execute_daily(self, run_id: str, cutoff: date) -> None:
        started = time.perf_counter()
        boards = self._boards()
        board_breadth = self._store.calculate_board_breadth_snapshots(cutoff)
        hotspot_snapshots = self._store.calculate_board_hotspot_snapshots(cutoff)
        benchmark = self._store.get_recent_daily_bars(
            "000001.SH", cutoff, DAILY_LOOKBACK_BARS,
        )
        run = self._store.get_signal_review_run(run_id)
        context_runs = self._store.list_compatible_prior_signal_review_runs(run_id, 7)
        correction_run = next(
            (item for item in context_runs if item["effective_date"] == cutoff), None,
        )
        session_runs = [
            item for item in context_runs if item["effective_date"] < cutoff
        ][:5]
        correction_observations = _daily_observations_by_symbol(
            self._store, correction_run,
        )
        recent_observations: dict[str, list[dict[str, object]]] = defaultdict(list)
        for context_run in session_runs:
            for item in self._store.list_board_daily_observations(
                run_id=str(context_run["run_id"]), limit=5000,
            ):
                recent_observations[str(item["symbol"])].append(item)
        prior_observations = {
            symbol: history[0] for symbol, history in recent_observations.items()
            if history
        }
        observations: list[dict[str, object]] = []
        hotspot_features: dict[str, dict[str, object]] = {}
        self._progress(run_id, "board-observations", len(boards), 0)
        for offset in range(0, len(boards), 100):
            page = boards[offset:offset + 100]
            series = self._store.get_recent_daily_bars_many(
                [str(board["symbol"]) for board in page], cutoff, DAILY_LOOKBACK_BARS,
            )
            batch = [
                analyze_daily_series(
                    str(board["symbol"]), series.get(str(board["symbol"]), []), cutoff,
                    benchmark_bars=benchmark,
                )
                for board in page
            ]
            for board in page:
                symbol = str(board["symbol"])
                hotspot_features[symbol] = extract_board_hotspot_features(
                    series.get(symbol, []), benchmark,
                    breadth_snapshot=board_breadth.get(symbol),
                    member_snapshot=hotspot_snapshots.get(symbol),
                )
            for observation in batch:
                metrics = observation.get("metrics")
                if isinstance(metrics, dict):
                    metrics["board_breadth"] = board_breadth.get(
                        str(observation["symbol"]), {"status": "unavailable"},
                    )
                prior = prior_observations.get(str(observation["symbol"]))
                observation.update(
                    build_board_analysis_record(
                        observation, _daily_prior_payload(prior),
                        [
                            payload
                            for item in recent_observations.get(
                                str(observation["symbol"]), [],
                            )
                            if (payload := _daily_prior_payload(item)) is not None
                        ],
                        _daily_prior_payload(correction_observations.get(
                            str(observation["symbol"]),
                        )),
                    )
                )
                _apply_transition_attention(observation)
            self._store.save_board_daily_observations(run_id, batch)
            observations.extend(batch)
            done = min(offset + len(page), len(boards))
            self._progress(run_id, "board-observations", len(boards), done)

        attention_registry = {
            str(item["symbol"]): item
            for item in self._store.list_signal_attention(DAILY_MARKET_BOARD_SIGNAL)
        }
        for observation in observations:
            symbol = str(observation["symbol"])
            if bool(observation["attention_eligible"]):
                self._store.promote_signal_attention(
                    DAILY_MARKET_BOARD_SIGNAL, symbol, cutoff,
                    [str(item) for item in observation["attention_reasons"]],
                )
        active_symbols = {
            str(observation["symbol"]) for observation in observations
            if bool(observation["attention_eligible"])
        }
        cooldown_through = _cooldown_through(self._store, cutoff, 5)
        for symbol, registry in attention_registry.items():
            if symbol in active_symbols or bool(registry.get("manual_pinned")):
                continue
            self._store.advance_signal_attention_lifecycle(
                DAILY_MARKET_BOARD_SIGNAL, symbol, cutoff, cooldown_through,
            )

        attention_registry = {
            str(item["symbol"]): item
            for item in self._store.list_signal_attention(DAILY_MARKET_BOARD_SIGNAL)
        }
        promoted = [
            observation for observation in observations
            if bool(observation["attention_eligible"])
            or str(observation["symbol"]) in attention_registry
        ]

        deep_results = self._run_daily_deep_analysis(
            run_id, cutoff, promoted, attention_registry,
            correction_observations or prior_observations,
        )
        for observation in promoted:
            symbol = str(observation["symbol"])
            if not _apply_deep_structural_scenario(
                observation, deep_results.get(symbol)
            ):
                continue
            prior = prior_observations.get(symbol)
            observation.update(build_board_analysis_record(
                observation, _daily_prior_payload(prior),
                [
                    payload for item in recent_observations.get(symbol, [])
                    if (payload := _daily_prior_payload(item)) is not None
                ],
                _daily_prior_payload(correction_observations.get(symbol)),
            ))
            self._store.update_board_daily_structural_scenario(
                run_id, symbol, metrics=observation["metrics"],
                conclusion_code=str(observation["conclusion_code"]),
                rendered_summary=str(observation["rendered_summary"]),
                comparison=observation["comparison"],
            )

        scorers = default_scorer_registry()
        board_names = {str(board["symbol"]): str(board["name"]) for board in boards}
        board_themes = self._store.resolve_board_theme_profiles(board_names)
        board_capacities = classify_board_capacities(
            self._store.calculate_board_capacity_snapshots(cutoff), board_names,
            board_themes,
        )
        turnover = self._store.get_market_turnover_proxy(cutoff)
        market_liquidity = (
            analyze_market_liquidity(turnover)
            if len(turnover) >= 25 else analyze_benchmark_volume_fallback(benchmark)
        )
        score_context_runs = ([correction_run] if correction_run else []) + session_runs
        trend_prior, trend_recent = _score_history(
            self._store, score_context_runs, TREND_BREAKOUT_SCORER,
            scorers.get(TREND_BREAKOUT_SCORER).version,
        )
        hotspot_system = BoardHotspotSystem()
        hotspot_prior, hotspot_recent = _score_history(
            self._store, score_context_runs, hotspot_system.system_id,
            hotspot_system.version,
        )
        observation_systems = _board_observation_system_registry()
        system_executions = observation_systems.execute_all(ObservationSystemContext(
            observations=observations,
            prior_scores={
                TREND_BREAKOUT_SCORER: trend_prior,
                BOARD_HOTSPOT_SYSTEM: hotspot_prior,
            },
            recent_scores={
                TREND_BREAKOUT_SCORER: trend_recent,
                BOARD_HOTSPOT_SYSTEM: hotspot_recent,
            },
            dependencies={
                "review_scorer_registry": scorers,
                "board_hotspot_features": hotspot_features,
                "board_names": board_names,
                "market_liquidity_context": market_liquidity,
                "board_capacity_features": board_capacities,
                "board_theme_profiles": board_themes,
            },
        ))
        system_execution_by_id = {
            execution.system_id: execution for execution in system_executions
        }
        trend_execution = system_execution_by_id[TREND_BREAKOUT_SCORER]
        hotspot_execution = system_execution_by_id[BOARD_HOTSPOT_SYSTEM]
        trend_scores = trend_execution.results
        hotspot_scores = hotspot_execution.results
        prior_wave_snapshots = self._store.list_hotspot_wave_snapshots(
            str(session_runs[0]["run_id"]), status="active",
        ) if session_runs else []
        hotspot_wave_snapshots = project_hotspot_waves(
            hotspot_scores, prior_wave_snapshots, cutoff,
            self._store.hotspot_wave_sequences_before(cutoff),
        )
        trend_score_by_symbol = {
            str(value["symbol"]): value for value in trend_scores
        }
        previous_items = {
            str(item["item_key"]): item
            for item in self._store.get_prior_signal_review_items(run_id)
            if bool(item["active"])
        }
        prior_session_items = _daily_items_by_key(self._store, session_runs[0]) if session_runs else {}
        correction_items = _daily_items_by_key(self._store, correction_run)
        recent_market_items: dict[str, list[dict[str, object]]] = defaultdict(list)
        for context_run in session_runs:
            for item in self._store.list_signal_review_items(str(context_run["run_id"])):
                if item.get("profile") == "market":
                    recent_market_items[str(item["item_key"])].append(item)
        emotion = self._store.calculate_market_emotion_snapshot(run_id, cutoff)
        items = self._daily_market_items(
            cutoff, previous_items, emotion, prior_session_items,
            correction_items, recent_market_items,
        )
        market_prior, market_recent = _score_history(
            self._store, score_context_runs, MARKET_REGIME_SCORER,
            scorers.get(MARKET_REGIME_SCORER).version,
        )
        market_execution = execute_scorer(
            scorers.get(MARKET_REGIME_SCORER), items,
            prior_by_symbol=market_prior, recent_by_symbol=market_recent,
        )
        market_scores = market_execution.results
        for system_id, execution in (
            (TREND_BREAKOUT_SCORER, trend_execution),
            (BOARD_HOTSPOT_SYSTEM, hotspot_execution),
            (MARKET_REGIME_SCORER, market_execution),
        ):
            if execution.error:
                LOGGER.warning(
                    "signal_review_scorer_failed run_id=%s system_id=%s error=%s",
                    run_id, system_id, execution.error,
                )
        market_score_by_key = {
            str(value["entity_key"]): value for value in market_scores
        }
        for item in items:
            score_result = market_score_by_key.get(str(item["item_key"]))
            if score_result:
                item["payload"]["score_result"] = score_result
                item["score"] = float(score_result["total_score"]) / 100
        for observation in promoted:
            symbol = str(observation["symbol"])
            item_key = f"attention:{symbol}"
            metrics = observation.get("metrics", {})
            reasons = list(observation.get("attention_reasons", []))
            score_result = trend_score_by_symbol.get(symbol)
            deep_result = deep_results.get(symbol)
            items.append({
                "item_id": str(uuid4()), "item_key": item_key,
                "rank": 0, "symbol": symbol, "profile": "attention",
                "change_type": "retained" if item_key in previous_items else "added",
                "active": True, "score": (
                    float(score_result["total_score"]) / 100
                    if score_result else _daily_attention_score(observation)
                ),
                "confidence": _daily_confidence(observation),
                "payload": {
                    "conclusion_code": observation["conclusion_code"],
                    "state_codes": observation["state_codes"],
                    "attention_reasons": reasons,
                    "rendered_summary": observation["rendered_summary"],
                    "metrics": metrics,
                    "comparison": observation["comparison"],
                    "effective_date": cutoff.isoformat(),
                    "observation_input_digest": observation["input_digest"],
                    "deep_analysis_state": observation["deep_analysis_state"],
                    "deep_analysis_run_id": (
                        deep_result.get("run_id") if deep_result else None
                    ),
                    "board_name": board_names.get(symbol, symbol),
                    **({"score_result": score_result} if score_result else {}),
                },
                "evidence": [{
                    "evidence_id": str(uuid4()), "alias": "",
                    "evidence_type": "board-daily-observation",
                    "payload": {
                        "symbol": symbol, "effective_date": cutoff.isoformat(),
                        "input_digest": observation["input_digest"],
                        "state_codes": observation["state_codes"],
                    },
                }, *_deep_analysis_evidence(deep_result)],
            })
        items.extend(_removed_daily_items(items, previous_items))
        _rank_daily_items(items)
        _assign_evidence_aliases(items)
        _attach_daily_evidence_references(items)
        complete_count = sum(item["coverage_state"] == "complete" for item in observations)
        missing_count = len(observations) - complete_count
        summary = {
            "expected_board_count": len(boards),
            "saved_observation_count": len(observations),
            "complete_observation_count": complete_count,
            "missing_observation_count": missing_count,
            "promoted_board_count": len(promoted),
            "deep_analyzed_count": sum(
                result.get("status") == "succeeded" for result in deep_results.values()
            ),
            "deep_deferred_count": sum(
                observation.get("deep_analysis_state") == "deferred-resource-limit"
                for observation in promoted
            ),
            "displayed_item_count": sum(bool(item["active"]) for item in items),
            "attention_registry_count": len(self._store.list_signal_attention(
                DAILY_MARKET_BOARD_SIGNAL,
            )),
            "emotion": emotion,
            "scoring_systems": scorers.definitions(),
            "observation_systems": observation_systems.definitions(),
            "trend_opportunity_count": sum(
                bool(value["eligible"]) for value in trend_scores
            ),
            "hotspot_candidate_count": sum(
                bool(value["eligible"]) for value in hotspot_scores
            ),
            "hotspot_active_wave_count": sum(
                value["status"] == "active" for value in hotspot_wave_snapshots
            ),
            "scoring_errors": [
                {"system_id": system_id, "error": execution.error}
                for system_id, execution in (
                    (TREND_BREAKOUT_SCORER, trend_execution),
                    (BOARD_HOTSPOT_SYSTEM, hotspot_execution),
                    (MARKET_REGIME_SCORER, market_execution),
                ) if execution.error
            ],
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "ai_used": False,
        }
        prior_pool_run = correction_run or (session_runs[0] if session_runs else None)
        prior_pool = self._store.get_observation_pool_snapshot(
            str(prior_pool_run["run_id"]), "board",
        ) if prior_pool_run else None
        board_pool = _build_board_pool_snapshot(
            self._store, run_id, cutoff, trend_scores,
            self._store.list_signal_attention(DAILY_MARKET_BOARD_SIGNAL),
            prior_pool, hotspot_scores=hotspot_scores,
        )
        summary["board_pool_count"] = len(board_pool["items"])
        prior_member_scan = self._store.get_observation_pool_snapshot(
            str(prior_pool_run["run_id"]), "stock",
        ) if prior_pool_run else None
        member_scan = build_board_member_scan_snapshot(
            self._store, run_id, cutoff, board_pool, prior_member_scan,
            progress=lambda total, done: self._progress(
                run_id, "stock-member-scan", total, done,
            ),
        )
        summary["board_member_scan_count"] = member_scan["summary"]["item_count"]
        summary["board_member_eligible_count"] = member_scan["summary"]["eligible_count"]
        independent_records = scan_full_market_independent_strength(
            self._store, cutoff,
            progress=lambda total, done: self._progress(
                run_id, "independent-stock-scan", total, done,
            ),
            seed_records=[
                item["payload"] for item in member_scan["items"]
                if item["payload"].get("coverage_state") == "complete"
            ],
        )
        latest_screener = self._store.get_latest_succeeded_screener_run(cutoff)
        screener_candidates = (
            self._store.list_screener_candidates(str(latest_screener["run_id"]))
            if latest_screener else []
        )
        stock_pool = build_unified_stock_pool_snapshot(
            run_id, cutoff, member_scan, independent_records,
            screener_run=latest_screener,
            screener_candidates=screener_candidates,
            manual_attention=[
                entry for entry in self._store.list_signal_attention(STOCK_OBSERVATION_SIGNAL)
                if self._store.get_instrument_kind(str(entry["symbol"])) is InstrumentKind.STOCK
            ],
            prior_snapshot=prior_member_scan,
        )
        assign_stock_presentation_layers(stock_pool)
        stock_m4_summary = self._run_stock_pool_analysis(run_id, cutoff, stock_pool)
        stock_prior, stock_recent = _score_history(
            self._store, score_context_runs, STOCK_OPPORTUNITY_SCORER,
            scorers.get(STOCK_OPPORTUNITY_SCORER).version,
        )
        stock_execution = execute_scorer(
            scorers.get(STOCK_OPPORTUNITY_SCORER),
            [{"symbol": item["symbol"], "payload": item["payload"]}
             for item in stock_pool["items"] if item["lifecycle_state"] != "cooldown"],
            prior_by_symbol=stock_prior, recent_by_symbol=stock_recent,
        )
        stock_scores = stock_execution.results
        stock_score_by_symbol = {str(score["symbol"]): score for score in stock_scores}
        for item in stock_pool["items"]:
            score = stock_score_by_symbol.get(str(item["symbol"]))
            if score is not None:
                item["payload"]["opportunity_score"] = score
        presentation = assign_stock_presentation_layers(stock_pool)
        summary["stock_pool_count"] = stock_pool["summary"]["item_count"]
        summary["independent_stock_candidate_count"] = stock_pool["summary"]["independent_count"]
        summary["stock_m4_analysis"] = stock_m4_summary
        summary["stock_opportunity_count"] = sum(
            bool(score["eligible"]) for score in stock_scores
        )
        summary["stock_presentation"] = presentation
        if stock_execution.error:
            summary["scoring_errors"].append({
                "system_id": STOCK_OPPORTUNITY_SCORER,
                "error": stock_execution.error,
            })
        digest = _result_digest(items + [{
            "active": True, "item_key": "all-board-observations",
            "rank": 0, "score": 0, "confidence": 1,
            "payload": {
                "digests": sorted(observation_digest(item) for item in observations),
            },
            "evidence": [],
        }])
        self._store.complete_signal_review_run(
            run_id, items=items, summary=summary, input_digest=digest,
            scores=[*trend_scores, *hotspot_scores, *market_scores, *stock_scores],
            pool_snapshots=[board_pool, stock_pool],
            hotspot_wave_snapshots=hotspot_wave_snapshots,
        )
        LOGGER.info(
            "daily_signal_review_completed run_id=%s date=%s observations=%s promoted=%s elapsed_ms=%.1f",
            run_id, cutoff, len(observations), len(promoted),
            (time.perf_counter() - started) * 1000,
        )

    def _run_stock_pool_analysis(
        self, run_id: str, cutoff: date, stock_pool: dict[str, object],
    ) -> dict[str, object]:
        from stock_harness.analysis_inputs import AnalysisHorizons, AnalysisTimeframe
        from stock_harness.pattern_analysis import PatternAnalysisRequest, PatternAnalysisService

        items = [
            item for item in stock_pool.get("items", []) if isinstance(item, dict)
        ]
        eligible = [
            item for item in items
            if item.get("lifecycle_state") not in {"cooldown", "invalidated"}
        ]
        limit = int(_daily_run_parameters()["stock_deep_analysis_limit"])
        selected = select_stock_m4_candidates(eligible, limit)
        selected_symbols = {str(item["symbol"]) for item in selected}
        for item in eligible:
            if str(item["symbol"]) in selected_symbols:
                continue
            payload = item.get("payload")
            if isinstance(payload, dict):
                payload["m4_analysis"] = {
                    "state": "deferred-resource-limit", "limit": limit,
                }
                payload["opportunity_classification"] = _stock_opportunity_classification(
                    payload, None,
                )
        service = PatternAnalysisService(self._store)
        confirmed = reused = failed = 0
        self._progress(run_id, "stock-m4-analysis", len(selected), 0)
        for index, item in enumerate(selected, 1):
            symbol = str(item["symbol"])
            payload = item.get("payload")
            if not isinstance(payload, dict):
                continue
            try:
                result = _reusable_stock_analysis(self._store, payload, cutoff)
                was_reused = result is not None
                if result is None:
                    result = service.analyze(PatternAnalysisRequest(
                        symbol=symbol,
                        timeframes=(AnalysisTimeframe.DAILY,),
                        horizons=AnalysisHorizons(14, 28, 250),
                        config_version="signal-review-stock-v1",
                        include_preview=False, as_of_date=cutoff,
                    ))[0]
                scenario = project_scenario_summary([
                    value for value in result.get("items", []) if isinstance(value, dict)
                ], direction="long")
                core_ids = read_core_structural_item_ids(result.get("items", []))
                payload["m4_analysis"] = {
                    "state": "reused" if was_reused else "confirmed",
                    "run_id": result["run_id"], "status": result.get("status"),
                    "algorithm_version": result.get("algorithm_version"),
                    "config_version": result.get("config_version"),
                    "scenario": scenario, "core_item_ids": core_ids,
                    "warning_count": len(result.get("warnings", [])),
                }
                payload["opportunity_classification"] = _stock_opportunity_classification(
                    payload, scenario,
                )
                item.setdefault("sources", []).append(_pool_source(
                    "m4-analysis", str(result["run_id"]), symbol,
                    "reused-screener-analysis" if was_reused else "bounded-pool-analysis",
                    {
                        "scenario_item_id": scenario.get("scenario_item_id"),
                        "state": scenario.get("state"),
                        "core_item_ids": core_ids,
                    },
                ))
                confirmed += 1
                reused += int(was_reused)
            except Exception as error:
                payload["m4_analysis"] = {
                    "state": "failed", "error_type": type(error).__name__,
                    "message": str(error)[:500],
                }
                payload["opportunity_classification"] = _stock_opportunity_classification(
                    payload, None,
                )
                failed += 1
                LOGGER.warning(
                    "stock_pool_m4_analysis_failed run_id=%s symbol=%s error_type=%s",
                    run_id, symbol, type(error).__name__,
                )
            self._progress(run_id, "stock-m4-analysis", len(selected), index)
        return {
            "limit": limit, "selected_count": len(selected),
            "confirmed_count": confirmed, "reused_count": reused,
            "failed_count": failed,
            "deferred_count": max(0, len(eligible) - len(selected)),
        }

    def _run_daily_deep_analysis(
        self, run_id: str, cutoff: date,
        promoted: list[dict[str, object]],
        registry: dict[str, dict[str, object]],
        queue_history: dict[str, dict[str, object]],
    ) -> dict[str, dict[str, object]]:
        from stock_harness.analysis_inputs import AnalysisHorizons, AnalysisTimeframe
        from stock_harness.pattern_analysis import (
            PatternAnalysisRequest,
            PatternAnalysisService,
        )

        ordered = _order_daily_deep_candidates(
            promoted, registry, queue_history, cutoff,
        )
        limit = int(_daily_run_parameters()["deep_analysis_limit"])
        selected = ordered[:limit]
        for observation in ordered[limit:]:
            observation["deep_analysis_state"] = "deferred-resource-limit"
            self._store.update_board_daily_deep_analysis(
                run_id, str(observation["symbol"]),
                state="deferred-resource-limit", source_run_id=None,
                summary={"reason": "daily deep-analysis resource limit", "limit": limit},
            )
        results: dict[str, dict[str, object]] = {}
        service = PatternAnalysisService(self._store)
        self._progress(run_id, "board-deep-analysis", len(selected), 0)
        for index, observation in enumerate(selected, 1):
            symbol = str(observation["symbol"])
            if observation.get("coverage_state") != "complete":
                state = "rejected-insufficient-coverage"
                summary = {"reason": "first-level daily history is incomplete"}
                self._store.update_board_daily_deep_analysis(
                    run_id, symbol, state=state, source_run_id=None, summary=summary,
                )
                observation["deep_analysis_state"] = state
                self._progress(run_id, "board-deep-analysis", len(selected), index)
                continue
            try:
                result = service.analyze(PatternAnalysisRequest(
                    symbol=symbol,
                    timeframes=(AnalysisTimeframe.DAILY,),
                    horizons=AnalysisHorizons(14, 28, 250),
                    config_version="signal-review-daily-v1", include_preview=False,
                    as_of_date=cutoff,
                ))[0]
                relevant = [
                    item for item in result.get("items", [])
                    if item.get("item_type") in {"line", "zone", "pattern", "transition"}
                ]
                summary = {
                    "status": result.get("status"),
                    "item_count": len(result.get("items", [])),
                    "relevant_item_count": len(relevant),
                    "warning_count": len(result.get("warnings", [])),
                }
                state = "confirmed" if relevant else "completed-no-structural-evidence"
                source_run_id = str(result["run_id"])
                results[symbol] = result
                self._store.update_board_daily_deep_analysis(
                    run_id, symbol, state=state, source_run_id=source_run_id,
                    summary=summary,
                )
                observation["deep_analysis_state"] = state
            except Exception as error:
                state = "failed"
                summary = {"error_type": type(error).__name__, "message": str(error)[:500]}
                self._store.update_board_daily_deep_analysis(
                    run_id, symbol, state=state, source_run_id=None, summary=summary,
                )
                observation["deep_analysis_state"] = state
                LOGGER.warning(
                    "daily_signal_deep_analysis_failed run_id=%s symbol=%s error_type=%s",
                    run_id, symbol, type(error).__name__,
                )
            self._progress(run_id, "board-deep-analysis", len(selected), index)
        return results

    def _daily_market_items(
        self, cutoff: date, previous: dict[str, dict[str, object]],
        emotion: dict[str, object],
        prior_session: dict[str, dict[str, object]],
        correction: dict[str, dict[str, object]],
        recent: dict[str, list[dict[str, object]]],
    ) -> list[dict[str, object]]:
        benchmark_bars = self._store.get_recent_daily_bars(
            "000001.SH", cutoff, DAILY_LOOKBACK_BARS,
        )
        market_observations = {}
        for symbol in ("000001.SH", "SHAMV.A"):
            bars = benchmark_bars if symbol == "000001.SH" else (
                self._store.get_recent_daily_bars(symbol, cutoff, DAILY_LOOKBACK_BARS)
            )
            market_observations[symbol] = analyze_daily_series(
                symbol, bars, cutoff, benchmark_bars=benchmark_bars,
                volume_semantics=(
                    "synthetic-not-traded" if symbol == "SHAMV.A" else "traded"
                ),
            )
        active_observation = market_observations["SHAMV.A"]
        if isinstance(active_observation.get("metrics"), dict):
            diagnostics = self._store.list_active_market_value_diagnostics(cutoff, cutoff)
            active_observation["metrics"]["active_market_value_diagnostics"] = (
                {**diagnostics[0], "trade_date": str(diagnostics[0]["trade_date"])}
                if diagnostics else {"status": "unavailable"}
            )
        divergence = _market_style_divergence(market_observations)
        items = []
        for rank, symbol in enumerate(("000001.SH", "SHAMV.A"), 1):
            observation = market_observations[symbol]
            item_key = f"market:{symbol}"
            analysis = build_board_analysis_record(
                observation, prior_session.get(item_key), recent.get(item_key, []),
                correction.get(item_key),
            )
            comparison = dict(analysis["comparison"])
            if symbol == "000001.SH":
                comparison["emotion"] = _emotion_transition(
                    emotion, prior_session.get(item_key),
                )
            code = str(analysis["conclusion_code"])
            rendered = str(analysis["rendered_summary"])
            rendered = rendered.replace(
                "- 近期对比：",
                f"- 风格：{_market_divergence_sentence(divergence)}\n- 近期对比：",
            )
            if symbol == "000001.SH":
                rendered = rendered.replace(
                    "- 近期对比：",
                    f"- 情绪：{_render_emotion(emotion)}"
                    f"（较上一交易日{_comparison_label(str(comparison['emotion']))}）\n"
                    "- 近期对比：",
                )
            items.append({
                "item_id": str(uuid4()), "item_key": item_key,
                "rank": rank, "symbol": symbol, "profile": "market",
                "change_type": "retained" if f"market:{symbol}" in previous else "added",
                "active": True, "score": _daily_attention_score(observation),
                "confidence": _daily_confidence(observation),
                "payload": {
                    "conclusion_code": code, "state_codes": observation["state_codes"],
                    "rendered_summary": rendered, "metrics": observation["metrics"],
                    "effective_date": cutoff.isoformat(),
                    "comparison": comparison,
                    "market_style_divergence": divergence,
                    "emotion": emotion if symbol == "000001.SH" else {
                        "status": "represented-by-market-overview",
                    },
                },
                "evidence": [{
                    "evidence_id": str(uuid4()), "alias": "",
                    "evidence_type": "market-daily-series",
                    "payload": {"symbol": symbol, "effective_date": cutoff.isoformat()},
                }, {
                    "evidence_id": str(uuid4()), "alias": "",
                    "evidence_type": "market-style-divergence",
                    "payload": divergence,
                }],
            })
        return items

    def _execute(self, run_id: str, cutoff: date) -> None:
        started = time.perf_counter()
        boards = self._boards()
        memberships: dict[str, list[dict[str, object]]] = {}
        stocks: dict[str, dict[str, object]] = {}
        total = len(boards)
        self._progress(run_id, "memberships", total, 0)
        for index, board in enumerate(boards, 1):
            members = [
                item for item in self._store.list_board_members(str(board["symbol"]), 5000)
                if item.get("available") is not False and item.get("kind") == "stock"
                and not is_risk_name(str(item.get("name") or ""))
            ]
            memberships[str(board["symbol"])] = members
            stocks.update((str(item["symbol"]), item) for item in members)
            if index % 50 == 0 or index == len(boards):
                self._progress(run_id, "memberships", total, index)

        start_date = cutoff - timedelta(days=10 * 366 + 45)
        total = len(stocks)
        features = {}
        self._progress(run_id, "stock-features", total, 0)
        for index, symbol in enumerate(sorted(stocks), 1):
            bars = [_bar_payload(item) for item in self._store.get_daily_bars(
                symbol, start_date, cutoff
            )]
            feature = calculate_stock_features(symbol, bars)
            if feature is not None:
                features[symbol] = feature
            if index % 50 == 0 or index == total:
                self._progress(run_id, "stock-features", total, index)

        assignments: list[dict[str, object]] = []
        total = len(boards)
        self._progress(run_id, "board-ranking", total, 0)
        ranked_boards = 0
        for index, board in enumerate(boards, 1):
            symbol = str(board["symbol"])
            member_features = [
                features[str(item["symbol"])] for item in memberships[symbol]
                if str(item["symbol"]) in features
            ]
            board_bars = [_bar_payload(item) for item in self._store.get_daily_bars(
                symbol, start_date, cutoff
            )]
            board_returns = compact_returns(board_bars)
            rankings = {
                RECENT_PROFILE: rank_board_leaders(
                    member_features, board_returns, RECENT_PROFILE, 2
                ),
                HISTORICAL_PROFILE: rank_board_leaders(
                    member_features, board_returns, HISTORICAL_PROFILE, HISTORICAL_LIMIT
                ),
            }
            if all(len(value) >= 2 for value in rankings.values()):
                ranked_boards += 1
                names = {str(item["symbol"]): str(item["name"]) for item in memberships[symbol]}
                for profile, ranked in rankings.items():
                    for item in ranked:
                        assignments.append({
                            "profile": profile, "board_symbol": symbol,
                            "board_name": str(board["name"]),
                            "board_classification": _classification(board),
                            "member_symbol": item.symbol,
                            "member_name": names.get(item.symbol, item.symbol),
                            "rank": item.rank, "score": item.score,
                            "confidence": item.confidence, "components": item.components,
                        })
            if index % 50 == 0 or index == total:
                self._progress(run_id, "board-ranking", total, index)

        current = _aggregate_assignments(assignments)
        previous = {
            str(item["item_key"]): item
            for item in self._store.get_prior_signal_review_items(run_id)
            if bool(item["active"])
        }
        items = _compare_items(current, previous)
        scorers = default_scorer_registry()
        context_runs = self._store.list_compatible_prior_signal_review_runs(run_id, 5)
        recognition_prior, recognition_recent = _score_history(
            self._store, context_runs, RECOGNITION_SCORER,
            scorers.get(RECOGNITION_SCORER).version,
        )
        recognition_execution = execute_scorer(
            scorers.get(RECOGNITION_SCORER),
            [item for item in items if bool(item["active"])],
            prior_by_symbol=recognition_prior,
            recent_by_symbol=recognition_recent,
        )
        recognition_scores = recognition_execution.results
        if recognition_execution.error:
            LOGGER.warning(
                "signal_review_scorer_failed run_id=%s system_id=%s error=%s",
                run_id, RECOGNITION_SCORER, recognition_execution.error,
            )
        recognition_by_key = {
            str(value["entity_key"]): value for value in recognition_scores
        }
        for item in items:
            score_result = recognition_by_key.get(str(item["item_key"]))
            if score_result:
                item["payload"]["score_result"] = score_result
                item["score"] = float(score_result["total_score"]) / 100
        _assign_evidence_aliases(items)
        digest = _result_digest(items)
        summary = {
            "board_count": len(boards), "stock_count": len(stocks),
            "ranked_board_count": ranked_boards,
            "assignment_count": len(assignments),
            "membership_semantics": "current membership snapshot; not point-in-time history",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
        self._store.complete_signal_review_run(
            run_id, items=items, summary={
                **summary, "scoring_systems": scorers.definitions(),
                "scoring_errors": ([{
                    "system_id": RECOGNITION_SCORER,
                    "error": recognition_execution.error,
                }] if recognition_execution.error else []),
            }, input_digest=digest, scores=recognition_scores,
        )
        LOGGER.info(
            "signal_review_run_completed run_id=%s effective_date=%s boards=%s stocks=%s items=%s elapsed_ms=%.1f",
            run_id, cutoff, len(boards), len(stocks),
            sum(bool(item["active"]) for item in items),
            (time.perf_counter() - started) * 1000,
        )

    def _boards(self) -> list[dict[str, object]]:
        result: dict[str, dict[str, object]] = {}
        for classification in ("concept", "industry"):
            offset = 0
            while True:
                page = self._store.search_instruments(
                    classification=classification, active=True, limit=500, offset=offset,
                )
                result.update((
                    str(item["symbol"]), {**item, "signal_classification": classification}
                ) for item in page)
                if len(page) < 500:
                    break
                offset += len(page)
        return [result[key] for key in sorted(result)]

    def _progress(self, run_id: str, phase: str, total: int, done: int) -> None:
        self._store.update_signal_review_progress(
            run_id, phase=phase, work_total=total, work_done=done,
        )
        if done == total or (done and done % 500 == 0):
            LOGGER.info("signal_review_progress run_id=%s phase=%s done=%s total=%s",
                        run_id, phase, done, total)


def _score_history(
    store: SQLiteMarketDataStore,
    runs: list[dict[str, object]],
    system_id: str,
    scorer_version: str,
) -> tuple[dict[str, dict[str, object]], dict[str, list[dict[str, object]]]]:
    recent: dict[str, list[dict[str, object]]] = defaultdict(list)
    for run in runs[:5]:
        for score in store.list_signal_review_scores(
            str(run["run_id"]), system_id=system_id,
        ):
            if score.get("scorer_version") != scorer_version:
                continue
            key = str(score.get("entity_key") or score["symbol"])
            for history_key in {key, str(score["symbol"])}:
                recent[history_key].append(score)
    prior = {key: values[0] for key, values in recent.items() if values}
    return prior, recent


def _build_board_pool_snapshot(
    store: SQLiteMarketDataStore,
    run_id: str,
    effective_date: date,
    trend_scores: list[dict[str, object]],
    attention: list[dict[str, object]],
    prior_pool: dict[str, object] | None,
    hotspot_scores: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    candidates: dict[str, dict[str, object]] = {}

    def candidate(symbol: str) -> dict[str, object]:
        return candidates.setdefault(symbol, {
            "symbol": symbol, "sources": [], "score": None,
            "hotspot_score": None,
            "attention": None,
        })

    eligible = [score for score in trend_scores if bool(score.get("eligible"))][:20]
    for score in trend_scores:
        symbol = str(score["symbol"])
        active_events = [
            event for event in score.get("hard_events", [])
            if isinstance(event, dict) and event.get("state") != "resolved"
        ]
        if score not in eligible and not active_events:
            continue
        value = candidate(symbol)
        value["score"] = score
        if score in eligible:
            value["sources"].append(_pool_source(
                "trend-score", run_id, str(score["entity_key"]),
                "eligible-top-20", {"rank": score["rank"],
                                    "total_score": score["total_score"]},
            ))
        for event in active_events:
            value["sources"].append(_pool_source(
                "hard-anomaly", run_id, str(score["entity_key"]),
                str(event["event_type"]), event,
            ))
    for score in hotspot_scores or []:
        if not bool(score.get("eligible")):
            continue
        symbol = str(score["symbol"])
        value = candidate(symbol)
        value["hotspot_score"] = score
        value["sources"].append(_pool_source(
            "hotspot-emergence", run_id, str(score["entity_key"]),
            str(score.get("hotspot_stage") or "hotspot-candidate"), {
                "rank": score["rank"],
                "total_score": score["total_score"],
                "stage": score.get("hotspot_stage"),
                "direction": score.get("score_direction"),
                "candidate_streak": score.get("candidate_streak"),
            },
        ))
    for entry in attention:
        symbol = str(entry["symbol"])
        value = candidate(symbol)
        value["attention"] = entry
        value["sources"].append(_pool_source(
            "attention-registry", run_id, symbol, str(entry["status"]),
            {"manual_pinned": entry["manual_pinned"],
             "reasons": entry["reasons"]},
        ))

    for board_symbol, assignments in _recognition_by_board(
        store, effective_date, set(candidates),
    ).items():
        value = candidate(board_symbol)
        value["sources"].extend(assignments)

    prior_items = {
        str(item["symbol"]): item
        for item in (prior_pool or {}).get("items", [])
        if isinstance(item, dict)
    }
    ordered = sorted(candidates.values(), key=lambda value: (
        not bool((value.get("attention") or {}).get("manual_pinned")),
        -max(
            float((value.get("score") or {}).get("total_score", -1)),
            float((value.get("hotspot_score") or {}).get("total_score", -1)),
        ),
        str(value["symbol"]),
    ))
    items = []
    for rank, value in enumerate(ordered, 1):
        symbol = str(value["symbol"])
        attention_entry = value.get("attention") or {}
        score = value.get("score") or {}
        hotspot_score = value.get("hotspot_score") or {}
        if attention_entry.get("manual_pinned"):
            lifecycle = "manual-pinned"
        elif attention_entry.get("status") == "cooldown":
            lifecycle = "cooldown"
        elif symbol not in prior_items:
            lifecycle = "new"
        elif (score.get("comparison") or {}).get("state") in {"strengthened", "weakened"}:
            lifecycle = str(score["comparison"]["state"])
        elif hotspot_score.get("score_direction") == "strengthening":
            lifecycle = "strengthened"
        elif hotspot_score.get("score_direction") == "declining":
            lifecycle = "weakened"
        else:
            lifecycle = "active"
        items.append({
            "symbol": symbol, "lifecycle_state": lifecycle, "rank": rank,
            "payload": {
                "trend_score": score.get("total_score"),
                "trend_grade": score.get("grade"),
                "trend_eligible": bool(score.get("eligible")),
                "hotspot_score": hotspot_score.get("total_score"),
                "hotspot_grade": hotspot_score.get("grade"),
                "hotspot_eligible": bool(hotspot_score.get("eligible")),
                "hotspot_stage": hotspot_score.get("hotspot_stage"),
                "hotspot_direction": hotspot_score.get("score_direction"),
                "hotspot_peak_score": hotspot_score.get("peak_score"),
                "recognition_assignment_count": sum(
                    source["source_type"] == "recognition-assignment"
                    for source in value["sources"]
                ),
            },
            "sources": value["sources"],
        })
    return {
        "pool_kind": "board", "effective_date": effective_date,
        "algorithm_version": BOARD_POOL_VERSION,
        "summary": {
            "item_count": len(items), "eligible_score_limit": 20,
            "hotspot_admission_count": sum(
                bool((item["payload"]).get("hotspot_eligible")) for item in items
            ),
            "source_signal_run_id": run_id,
        },
        "items": items,
    }


def _recognition_by_board(
    store: SQLiteMarketDataStore, effective_date: date, board_symbols: set[str],
) -> dict[str, list[dict[str, object]]]:
    recognition_run = store.get_latest_succeeded_signal_review_run(
        WEEKLY_RECOGNITION_SIGNAL, effective_date,
    )
    if recognition_run is None:
        return {}
    result: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in store.list_signal_review_items(str(recognition_run["run_id"])):
        if not item["active"]:
            continue
        for evidence in item["evidence"]:
            if evidence["evidence_type"] != "board-recognition-ranking":
                continue
            payload = evidence["payload"]
            board_symbol = str(payload.get("board_symbol") or "")
            if board_symbol not in board_symbols:
                continue
            recognition_rank = int(payload.get("rank") or 0)
            result[board_symbol].append(_pool_source(
                "recognition-assignment", str(recognition_run["run_id"]),
                str(item["item_key"]), str(item["profile"]), {
                    "member_symbol": item["symbol"], "member_name": item["name"],
                    "rank": recognition_rank,
                    "recognition_role": f"{item['profile']}-rank-{recognition_rank}",
                    "score": payload.get("score"),
                    "confidence": payload.get("confidence"),
                    "board_name": payload.get("board_name"),
                },
            ))
    return result


def _pool_source(
    source_type: str, source_reference: str, source_entity_key: str,
    reason: str, payload: object,
) -> dict[str, object]:
    return {
        "source_type": source_type, "source_reference": source_reference,
        "source_entity_key": source_entity_key, "reason": reason,
        "payload": payload,
    }


def _reusable_stock_analysis(
    store: SQLiteMarketDataStore, payload: dict[str, object], cutoff: date,
) -> dict[str, object] | None:
    screener_results = payload.get("screener_results")
    if not isinstance(screener_results, list):
        return None
    for candidate in screener_results:
        if not isinstance(candidate, dict) or not candidate.get("analysis_run_id"):
            continue
        result = store.get_generated_analysis_run(str(candidate["analysis_run_id"]))
        if result is not None and result.get("as_of_date") == cutoff:
            return result
    return None


def _stock_opportunity_classification(
    payload: dict[str, object], scenario: dict[str, object] | None,
) -> dict[str, object]:
    recognized = bool(payload.get("recognized"))
    independent = payload.get("independent_scan")
    member = payload.get("member_scan")
    independent_eligible = any(
        bool(scan.get("eligible")) for scan in (independent, member)
        if isinstance(scan, dict)
    )
    state = str((scenario or {}).get("state") or "unavailable")
    targets = (
        scenario.get("targets", []) if isinstance(scenario, dict) else []
    )
    credible_targets = [
        target for target in targets
        if isinstance(target, dict)
        and isinstance(target.get("stressed_risk_reward_ratio"), (int, float))
        and float(target["stressed_risk_reward_ratio"]) >= 3.0
    ]
    opportunity_eligible = (
        state in {"waiting-trigger", "triggered", "retest"}
        and bool(credible_targets)
    )
    if recognized and opportunity_eligible:
        classification = "recognized-and-eligible"
    elif recognized:
        classification = "recognized-but-ineligible"
    elif independent_eligible and opportunity_eligible:
        classification = "emerging-core-candidate"
    elif opportunity_eligible:
        classification = "ordinary-structure-candidate"
    else:
        classification = "ordinary-observation"
    return {
        "classification": classification,
        "recognition_state": "recognized" if recognized else "unrecognized",
        "independent_strength_eligible": independent_eligible,
        "opportunity_state": state,
        "opportunity_eligible": opportunity_eligible,
        "credible_target_count": len(credible_targets),
        "minimum_stressed_risk_reward": 3.0,
    }


def _bar_payload(bar) -> dict[str, object]:
    return {
        "trade_date": bar.trade_date.isoformat(), "open": bar.open,
        "high": bar.high, "low": bar.low, "close": bar.close, "volume": bar.volume,
    }


def _classification(board: dict[str, object]) -> str:
    return str(board.get("signal_classification") or "concept")


def _run_parameters() -> dict[str, object]:
    return {
        "lookback_years": 10,
        "recent_rank": 1,
        "historical_rank_limit": HISTORICAL_LIMIT,
        "historical_gate": {
            "has_rank_one": True, "minimum_score": 0.80,
            "minimum_confidence": 0.60, "minimum_board_count": 2,
        },
        "manual_trigger": True,
        "membership_semantics": "current-membership-snapshot",
    }


def _daily_run_parameters() -> dict[str, object]:
    return {
        "lookback_bars": DAILY_LOOKBACK_BARS,
        "minimum_bars": 120,
        "attention_filter": DAILY_CONFIG_VERSION,
        "manual_trigger": True,
        "full_observation_persistence": True,
        "deep_analysis_limit": 60,
        "stock_deep_analysis_limit": 30,
        "stock_deep_analysis_allocator": STOCK_M4_ALLOCATOR_VERSION,
        "stock_presentation_version": STOCK_PRESENTATION_VERSION,
        "stock_focus_limit": STOCK_FOCUS_LIMIT,
        "stock_risk_limit": STOCK_RISK_LIMIT,
    }


def _daily_prior_payload(
    prior_observation: dict[str, object] | None,
) -> dict[str, object] | None:
    if prior_observation is None:
        return None
    conclusion_code = str(prior_observation.get("conclusion_code") or "")
    if not conclusion_code or conclusion_code == "data-unavailable":
        conclusion_code, _ = render_board_summary(prior_observation, None)
    return {
        "run_id": prior_observation.get("run_id"),
        "payload": {
            "conclusion_code": conclusion_code,
            "metrics": prior_observation.get("metrics", {}),
        },
        "effective_date": prior_observation.get("effective_date"),
    }


def _daily_observations_by_symbol(
    store: SQLiteMarketDataStore, run: dict[str, object] | None,
) -> dict[str, dict[str, object]]:
    if run is None:
        return {}
    return {
        str(item["symbol"]): item
        for item in store.list_board_daily_observations(
            run_id=str(run["run_id"]), limit=5000,
        )
    }


def _apply_transition_attention(observation: dict[str, object]) -> None:
    comparison = observation.get("comparison")
    transition = comparison.get("transition") if isinstance(comparison, dict) else None
    if transition not in {"strengthened", "weakened", "changed", "invalidated"}:
        return
    states = [str(item) for item in observation.get("state_codes", [])]
    reasons = [str(item) for item in observation.get("attention_reasons", [])]
    states.append("prior-state-transition")
    reasons.append(f"prior-state-{transition}")
    observation["state_codes"] = sorted(set(states))
    observation["attention_reasons"] = sorted(set(reasons))
    observation["attention_eligible"] = True
    observation["deep_analysis_state"] = "pending"


def _daily_items_by_key(
    store: SQLiteMarketDataStore, run: dict[str, object] | None,
) -> dict[str, dict[str, object]]:
    if run is None:
        return {}
    return {
        str(item["item_key"]): item
        for item in store.list_signal_review_items(str(run["run_id"]))
        if item.get("profile") == "market"
    }


def _cooldown_through(
    store: SQLiteMarketDataStore, effective_date: date, sessions: int,
) -> date:
    candidates = [
        item for item in store.list_trading_dates(
            "tushare", effective_date + timedelta(days=1),
            effective_date + timedelta(days=max(14, sessions * 3)),
        )
        if item > effective_date
    ]
    return candidates[sessions - 1] if len(candidates) >= sessions else (
        effective_date + timedelta(days=7)
    )


def _daily_attention_score(observation: dict[str, object]) -> float:
    reasons = {str(item) for item in observation.get("attention_reasons", [])}
    weights = {
        "1y-descending-envelope-broken": .28,
        "6m-descending-envelope-broken": .24,
        "3m-descending-envelope-broken": .20,
        "3m-descending-envelope-approaching": .14,
        "bullish-boundary-proximity": .20,
        "downside-exhaustion": .20,
        "oversold-rebound-triggered": .28,
        "sudden-volume-expansion": .16,
        "boundary-volume-contraction": .12,
        "relative-strength-regime": .10,
        "prior-state-strengthened": .18,
        "prior-state-weakened": .14,
        "prior-state-changed": .16,
        "prior-state-invalidated": .16,
    }
    return min(1.0, round(sum(weights.get(reason, .08) for reason in reasons), 6))


def _order_daily_deep_candidates(
    observations: list[dict[str, object]],
    registry: dict[str, dict[str, object]],
    history: dict[str, dict[str, object]],
    effective_date: date,
) -> list[dict[str, object]]:
    def key(observation: dict[str, object]) -> tuple[object, ...]:
        symbol = str(observation["symbol"])
        attention = registry.get(symbol, {})
        previous = history.get(symbol, {})
        return (
            not bool(attention.get("manual_pinned")),
            previous.get("deep_analysis_state") != "deferred-resource-limit",
            str(attention.get("first_observed_date") or effective_date),
            -_daily_attention_score(observation),
            symbol,
        )
    return sorted(observations, key=key)


def _daily_confidence(observation: dict[str, object]) -> float:
    if observation.get("coverage_state") != "complete":
        return 0.0
    reasons = len(observation.get("attention_reasons", []))
    disqualifiers = len(observation.get("disqualifiers", []))
    return max(0.0, min(1.0, round(.55 + min(reasons, 3) * .1 - disqualifiers * .2, 6)))


def _render_emotion(emotion: dict[str, object]) -> str:
    metrics = emotion.get("metrics")
    if not isinstance(metrics, dict) or emotion.get("status") == "unavailable":
        return "正式收盘情绪输入不可用，不以零值替代。"
    breadth = metrics.get("breadth")
    breadth_text = "不可用" if breadth is None else f"{float(breadth):+.2f}"
    if metrics.get("limit_up_count") is None:
        limit_text = "涨跌停价格覆盖不足"
    else:
        sealing = metrics.get("sealing_rate")
        sealing_text = "不可用" if sealing is None else f"{float(sealing) * 100:.1f}%"
        limit_text = (
            f"涨停{metrics['limit_up_count']}、跌停{metrics['limit_down_count']}、"
            f"破板{metrics['broken_up_count']}、封板率{sealing_text}"
        )
    return (
        f"上涨{metrics.get('advance_count', 0)}、下跌{metrics.get('decline_count', 0)}，"
        f"宽度{breadth_text}；{limit_text}。"
    )


def _emotion_transition(
    current: dict[str, object], prior_item: dict[str, object] | None,
) -> str:
    if prior_item is None:
        return "new"
    prior_payload = prior_item.get("payload")
    prior = prior_payload.get("emotion") if isinstance(prior_payload, dict) else None
    if not isinstance(prior, dict) or prior.get("status") == "unavailable":
        return "new"
    if current.get("status") == "unavailable":
        return "invalidated"
    current_score = _emotion_score(current)
    prior_score = _emotion_score(prior)
    if current_score is None or prior_score is None:
        return "changed"
    if abs(current_score - prior_score) <= .08:
        return "unchanged"
    return "strengthened" if current_score > prior_score else "weakened"


def _market_style_divergence(
    observations: dict[str, dict[str, object]],
) -> dict[str, object]:
    shanghai = observations.get("000001.SH", {})
    active = observations.get("SHAMV.A", {})
    if any(item.get("coverage_state") != "complete" for item in (shanghai, active)):
        return {"state": "unavailable", "difference_5": None, "difference_20": None}
    shanghai_metrics = shanghai.get("metrics")
    active_metrics = active.get("metrics")
    if not isinstance(shanghai_metrics, dict) or not isinstance(active_metrics, dict):
        return {"state": "unavailable", "difference_5": None, "difference_20": None}
    difference_5 = (
        _nested_metric(active_metrics, "returns", "5")
        - _nested_metric(shanghai_metrics, "returns", "5")
    )
    difference_20 = (
        _nested_metric(active_metrics, "returns", "20")
        - _nested_metric(shanghai_metrics, "returns", "20")
    )
    if difference_5 * difference_20 < 0 and abs(difference_5) >= .02:
        state = "short-cycle-rotation"
    elif difference_20 >= .04:
        state = "active-value-led"
    elif difference_20 <= -.04:
        state = "large-cap-led"
    else:
        state = "aligned"
    return {
        "state": state, "difference_5": round(difference_5, 6),
        "difference_20": round(difference_20, 6),
    }


def _nested_metric(metrics: dict[str, object], group: str, key: str) -> float:
    value = metrics.get(group)
    nested = value.get(key) if isinstance(value, dict) else None
    return float(nested) if isinstance(nested, (int, float)) else 0.0


def _market_divergence_sentence(divergence: dict[str, object]) -> str:
    state = str(divergence.get("state"))
    label = {
        "active-value-led": "活跃市值相对占优",
        "large-cap-led": "上证权重相对占优",
        "short-cycle-rotation": "5日与20日方向分歧，处于风格轮动",
        "aligned": "上证与活跃市值大致同步",
        "unavailable": "双指数覆盖不足，暂不判断",
    }.get(state, state)
    if state == "unavailable":
        return label
    return (
        f"{label}；活跃市值相对上证近5日"
        f"{float(divergence['difference_5']) * 100:+.2f}个百分点、近20日"
        f"{float(divergence['difference_20']) * 100:+.2f}个百分点。"
    )


def _emotion_score(snapshot: dict[str, object]) -> float | None:
    metrics = snapshot.get("metrics")
    if not isinstance(metrics, dict) or metrics.get("breadth") is None:
        return None
    components = [float(metrics["breadth"])]
    if metrics.get("limit_balance") is not None:
        components.append(float(metrics["limit_balance"]))
    if metrics.get("sealing_rate") is not None:
        components.append(float(metrics["sealing_rate"]) * 2 - 1)
    return sum(components) / len(components)


def _comparison_label(value: str) -> str:
    return {
        "new": "首次建立基线", "unchanged": "基本持平",
        "strengthened": "增强", "weakened": "减弱",
        "changed": "结构变化", "invalidated": "输入失效",
    }.get(value, value)


def _deep_analysis_evidence(
    result: dict[str, object] | None,
) -> list[dict[str, object]]:
    if not result or result.get("status") != "succeeded":
        return []
    source_run_id = str(result["run_id"])
    result_items = [
        item for item in result.get("items", []) if isinstance(item, dict)
    ]
    core_ids = set(read_core_structural_item_ids(result_items))
    scenario = next((item for item in result_items if (
        item.get("item_type") == "scenario"
        and isinstance(item.get("payload"), dict)
        and item["payload"].get("kind") == "structural-trade-scenario"
        and item["payload"].get("primary") is True
    )), None)
    if scenario is not None:
        core_ids.add(str(scenario["item_id"]))
        supply_id = scenario["payload"].get("overhead_supply_item_id")
        if isinstance(supply_id, str):
            core_ids.add(supply_id)
    evidence = []
    for item in result_items:
        if item.get("item_id") not in core_ids or item.get("item_type") not in {
            "line", "zone", "pattern", "transition", "evidence", "scenario",
        }:
            continue
        evidence.append({
            "evidence_id": str(uuid4()), "alias": "",
            "evidence_type": f"m4-{item['item_type']}",
            "source_run_id": source_run_id,
            "source_item_id": str(item["item_id"]),
            "payload": {
                "item_type": item["item_type"],
                "geometry": item.get("payload", {}),
            },
        })
    return evidence


def _apply_deep_structural_scenario(
    observation: dict[str, object], result: dict[str, object] | None,
) -> bool:
    if not result or result.get("status") != "succeeded":
        return False
    items = [item for item in result.get("items", []) if isinstance(item, dict)]
    summary = project_scenario_summary(items)
    if summary.get("scenario_item_id") is None:
        return False
    metrics = observation.get("metrics")
    if not isinstance(metrics, dict):
        return False
    metrics["price_space"] = summary
    return True


def _attach_daily_evidence_references(items: list[dict[str, object]]) -> None:
    labels = {
        "market-daily-series": "截止日K线",
        "market-style-divergence": "双指数风格差",
        "board-daily-observation": "一级量价形态",
        "m4-line": "趋势线",
        "m4-zone": "关键位",
        "m4-pattern": "形态",
        "m4-transition": "突破/破位",
    }
    for item in items:
        if item.get("profile") not in {"market", "attention"} or not item.get("active"):
            continue
        payload = item.get("payload")
        if not isinstance(payload, dict) or not payload.get("rendered_summary"):
            continue
        references = [
            f"[{evidence['alias']}] {labels.get(str(evidence['evidence_type']), str(evidence['evidence_type']))}"
            for evidence in item.get("evidence", [])[:4]
        ]
        if not references:
            continue
        summary = str(payload["rendered_summary"])
        evidence_line = f"- 证据：{'；'.join(references)}"
        payload["rendered_summary"] = summary.replace(
            "- 近期对比：", f"{evidence_line}\n- 近期对比：",
        )


def _removed_daily_items(
    current: list[dict[str, object]], previous: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    current_keys = {str(item["item_key"]) for item in current}
    removed = []
    for key, item in previous.items():
        if key in current_keys or str(item.get("profile")) == "market":
            continue
        removed.append({
            "item_id": str(uuid4()), "item_key": key, "rank": 0,
            "symbol": item["symbol"], "profile": item["profile"],
            "change_type": "removed", "active": False,
            "score": item["score"], "confidence": item["confidence"],
            "payload": {
                **dict(item.get("payload", {})),
                "transition": "invalidated-or-left-attention",
            },
            "evidence": [{
                **evidence, "evidence_id": str(uuid4()), "alias": "",
            } for evidence in item.get("evidence", [])],
        })
    return removed


def _rank_daily_items(items: list[dict[str, object]]) -> None:
    market_rank = 0
    attention_rank = 0
    for item in sorted(items, key=lambda value: (
        0 if value["profile"] == "market" else 1,
        not bool(value["active"]), -float(value["score"]), str(value["symbol"]),
    )):
        if item["profile"] == "market":
            market_rank += 1
            item["rank"] = market_rank
        else:
            attention_rank += 1
            item["rank"] = attention_rank


def _aggregate_assignments(assignments: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for item in assignments:
        profile = str(item["profile"])
        if profile == RECENT_PROFILE and int(item["rank"]) != 1:
            continue
        grouped[(profile, str(item["member_symbol"]))].append(item)
    selected: list[dict[str, object]] = []
    for (profile, symbol), rows in grouped.items():
        board_names = {str(item["board_name"]) for item in rows}
        max_score = max(float(item["score"]) for item in rows)
        max_confidence = max(float(item["confidence"]) for item in rows)
        has_rank_one = any(int(item["rank"]) == 1 for item in rows)
        if profile == HISTORICAL_PROFILE and not (
            has_rank_one and max_score >= 0.80 and max_confidence >= 0.60
            and len(board_names) >= 2
        ):
            continue
        ordered = sorted(rows, key=lambda item: (
            int(item["rank"]), -float(item["score"]), str(item["board_name"])
        ))
        evidence = [{
            "evidence_id": str(uuid4()), "alias": "",
            "evidence_type": "board-recognition-ranking",
            "payload": {
                "board_symbol": row["board_symbol"], "board_name": row["board_name"],
                "board_classification": row["board_classification"],
                "rank": row["rank"], "score": row["score"],
                "confidence": row["confidence"], "components": row["components"],
            },
        } for index, row in enumerate(ordered[:12], 1)]
        selected.append({
            "item_id": str(uuid4()), "item_key": f"{profile}:{symbol}",
            "symbol": symbol, "profile": profile, "score": max_score,
            "confidence": max_confidence,
            "payload": {
                "board_count": len(board_names),
                "rank_one_count": sum(int(item["rank"]) == 1 for item in rows),
                "board_names": sorted(board_names),
            },
            "evidence": evidence,
        })
    selected.sort(key=lambda item: (
        0 if item["profile"] == RECENT_PROFILE else 1,
        -float(item["score"]), -float(item["confidence"]), str(item["symbol"]),
    ))
    counters = defaultdict(int)
    for item in selected:
        counters[str(item["profile"])] += 1
        item["rank"] = counters[str(item["profile"])]
    _assign_evidence_aliases(selected)
    return selected


def _compare_items(
    current: list[dict[str, object]], previous: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    current_keys = {str(item["item_key"]) for item in current}
    result = [{
        **item, "active": True,
        "change_type": "retained" if item["item_key"] in previous else "added",
    } for item in current]
    removed_by_profile: dict[str, int] = defaultdict(int)
    for key, item in previous.items():
        if key in current_keys:
            continue
        profile = str(item["profile"])
        removed_by_profile[profile] += 1
        result.append({
            "item_id": str(uuid4()), "item_key": key,
            "rank": removed_by_profile[profile], "symbol": item["symbol"],
            "profile": profile, "change_type": "removed", "active": False,
            "score": item["score"], "confidence": item["confidence"],
            "payload": item["payload"], "evidence": item["evidence"],
        })
    return result


def _assign_evidence_aliases(items: list[dict[str, object]]) -> None:
    sequence = 0
    for item in items:
        for evidence in item.get("evidence", []):
            sequence += 1
            evidence["alias"] = f"S{sequence}"


def _result_digest(items: list[dict[str, object]]) -> str:
    normalized = []
    for item in items:
        if not bool(item["active"]):
            continue
        normalized.append({
            "item_key": item["item_key"], "rank": item["rank"],
            "score": item["score"], "confidence": item["confidence"],
            "payload": item.get("payload", {}),
            "evidence": [{
                "evidence_type": evidence["evidence_type"],
                "source_run_id": evidence.get("source_run_id"),
                "source_item_id": evidence.get("source_item_id"),
                "payload": evidence.get("payload", {}),
            } for evidence in item.get("evidence", [])],
        })
    return hashlib.sha256(json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=lambda value: value.isoformat() if isinstance(value, date) else str(value),
    ).encode("utf-8")).hexdigest()
