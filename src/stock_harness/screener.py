"""Bounded asynchronous stock screening with immutable result snapshots."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
import logging
import threading
import time
from typing import Sequence

from stock_harness.analysis_inputs import (
    AnalysisHorizons, AnalysisInputMode, AnalysisInputService, AnalysisTimeframe,
)
from stock_harness.major_descending_lines import (
    MajorLinePeriod, MajorLineState,
)
from stock_harness.pattern_analysis import PatternAnalysisRequest, PatternAnalysisService
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.accumulation_pattern import ANALYSIS_LOOKBACK
from stock_harness.first_pullback_pattern import CONFIG as PULLBACK_CONFIG
from stock_harness.low_base_pullback import (
    ALGORITHM_VERSION as LOW_BASE_VERSION, CONFIG as LOW_BASE_CONFIG, LAUNCH_TYPE as LOW_BASE_TYPE,
)
from stock_harness.screener_result_tags import attach_recognition_tags
from stock_harness.deep_drawdown_pattern import (
    STRATEGY_ID as DEEP_DRAWDOWN_STRATEGY_ID, ALGORITHM_VERSION as DEEP_DRAWDOWN_VERSION,
    KIND as DEEP_DRAWDOWN_KIND, STATE as DEEP_DRAWDOWN_STATE, MIN_SCORE,
    REFERENCE_SYMBOL, REFERENCE_START, REFERENCE_END,
)
from stock_harness.bull_flag_pattern import (
    STRATEGY_ID as BULL_FLAG_STRATEGY_ID, ALGORITHM_VERSION as BULL_FLAG_VERSION,
    CONFIG as BULL_FLAG_CONFIG, KIND as BULL_FLAG_KIND,
)
from stock_harness.volume_accumulation import (
    STATE as ACCUMULATION_STATE,
    STRATEGY_ID as ACCUMULATION_STRATEGY_ID,
    STRATEGY_VERSION as ACCUMULATION_STRATEGY_VERSION,
    VolumeAccumulationSignal,
    detect_volume_accumulation, accumulation_rank_key,
)


LOGGER = logging.getLogger(__name__)
STRATEGY_ID = "major-descending-breakout"
STRATEGY_VERSION = "major-descending-breakout-v5"
CONFIG_VERSION = "screener-major-descending-v5"
PULLBACK_STRATEGY_ID = "strong-first-pullback"
PULLBACK_STRATEGY_VERSION = "strong-first-pullback-v3"
LOW_BASE_STRATEGY_ID = "low-base-platform-pullback"
DEFAULT_HORIZONS = AnalysisHorizons(60, 120, 250)
SCREENABLE_STATES = (
    MajorLineState.CRITICAL_BREAKOUT,
    MajorLineState.BREAKOUT_RETEST,
    MajorLineState.BROKEN_OUT,
)
class ScreenerBusyError(RuntimeError):
    pass


class ScreenerService:
    def __init__(self, store: SQLiteMarketDataStore) -> None:
        self._store = store
        self._inputs = AnalysisInputService(store)
        self._analysis = PatternAnalysisService(store)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        recovered = store.recover_interrupted_screener_runs()
        if recovered:
            LOGGER.warning("screener_interrupted_runs_recovered count=%s", recovered)

    @staticmethod
    def strategies() -> list[dict[str, object]]:
        return [{
            "strategy_id": STRATEGY_ID,
            "name": "大斜边突破",
            "version": STRATEGY_VERSION,
            "periods": [MajorLinePeriod.HALF_YEAR.value, MajorLinePeriod.YEAR.value],
            "states": [item.value for item in SCREENABLE_STATES],
            "final_bars_only": True,
        }, {
            "strategy_id": ACCUMULATION_STRATEGY_ID,
            "name": "20日堆量蓄势",
            "version": ACCUMULATION_STRATEGY_VERSION,
            "window": 20,
            "context_window": 80,
            "states": [ACCUMULATION_STATE],
            "final_bars_only": True,
        }, {
            "strategy_id": LOW_BASE_STRATEGY_ID,
            "name": "低位平台回踩",
            "version": LOW_BASE_VERSION,
            "window": LOW_BASE_CONFIG.observation_window_sessions,
            "states": ["pullback-observation", "pullback-confirmed"],
            "final_bars_only": True,
        }, {
            "strategy_id": BULL_FLAG_STRATEGY_ID, "name": "牛旗盘整",
            "version": BULL_FLAG_VERSION, "window": BULL_FLAG_CONFIG.max_launch_age,
            "states": ["pullback-observation"], "final_bars_only": True,
        }, {
            "strategy_id": PULLBACK_STRATEGY_ID,
            "name": "强势股首次回踩",
            "version": PULLBACK_STRATEGY_VERSION,
            "window": PULLBACK_CONFIG.observation_window_sessions,
            "states": ["pullback-observation", "pullback-confirmed"],
            "final_bars_only": True,
        }, {
            "strategy_id": DEEP_DRAWDOWN_STRATEGY_ID, "name": "深跌缩量整理",
            "version": DEEP_DRAWDOWN_VERSION, "window": 60,
            "states": [DEEP_DRAWDOWN_STATE], "final_bars_only": True,
        }]

    def start_run(
        self,
        periods: Sequence[MajorLinePeriod],
        states: Sequence[MajorLineState],
        max_results: int,
        as_of_date: date | None = None,
        strategy_id: str = STRATEGY_ID,
    ) -> dict[str, object]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ScreenerBusyError("another screener run is already active")
            cutoff = as_of_date or self._store.get_latest_stock_daily_bar_date()
            if cutoff is None:
                raise ValueError("no completed stock daily bars are available")
            run = self._store.create_screener_run(
                strategy_id, _strategy_version(strategy_id), cutoff,
                _parameters(strategy_id, periods, states, max_results),
            )
            self._thread = threading.Thread(
                target=self._run_guarded,
                args=(str(run["run_id"]), cutoff, tuple(periods), tuple(states), max_results, strategy_id),
                name="stock-harness-screener", daemon=True,
            )
            self._thread.start()
            return run

    def run_sync(
        self,
        periods: Sequence[MajorLinePeriod],
        states: Sequence[MajorLineState],
        max_results: int,
        as_of_date: date,
        strategy_id: str = STRATEGY_ID,
    ) -> dict[str, object]:
        run = self._store.create_screener_run(
            strategy_id, _strategy_version(strategy_id), as_of_date,
            _parameters(strategy_id, periods, states, max_results),
        )
        try:
            self._execute(
                str(run["run_id"]), as_of_date, periods, states,
                max_results, strategy_id,
            )
        except Exception as error:
            self._store.fail_screener_run(str(run["run_id"]), str(error))
            raise
        return self._store.get_screener_run(str(run["run_id"]))  # type: ignore[return-value]

    def _run_guarded(
        self, run_id: str, cutoff: date, periods: Sequence[MajorLinePeriod],
        states: Sequence[MajorLineState], max_results: int, strategy_id: str,
    ) -> None:
        try:
            self._execute(run_id, cutoff, periods, states, max_results, strategy_id)
        except Exception as error:
            self._store.fail_screener_run(run_id, str(error))
            LOGGER.exception("screener_run_failed run_id=%s", run_id)

    def _execute(
        self, run_id: str, cutoff: date, periods: Sequence[MajorLinePeriod],
        states: Sequence[MajorLineState], max_results: int, strategy_id: str,
    ) -> None:
        if strategy_id in (PULLBACK_STRATEGY_ID, LOW_BASE_STRATEGY_ID, BULL_FLAG_STRATEGY_ID, DEEP_DRAWDOWN_STRATEGY_ID):
            self._execute_first_pullback(run_id, cutoff, max_results, strategy_id)
            return
        if strategy_id == ACCUMULATION_STRATEGY_ID:
            self._execute_volume_accumulation(run_id, cutoff, max_results)
            return
        if strategy_id != STRATEGY_ID:
            raise ValueError(f"unknown screener strategy: {strategy_id}")
        self._execute_major_descending(run_id, cutoff, periods, states, max_results)

    def _execute_major_descending(
        self, run_id: str, cutoff: date, periods: Sequence[MajorLinePeriod],
        states: Sequence[MajorLineState], max_results: int,
    ) -> None:
        started = time.perf_counter()
        universe = self._store.list_active_stock_symbols_for_screening()
        state_set = set(states).intersection(SCREENABLE_STATES)
        period_set = {period.value for period in periods}
        candidates: list[dict[str, object]] = []
        self._store.update_screener_progress(
            run_id, universe_count=len(universe), scanned_count=0
        )
        LOGGER.info(
            "screener_run_started run_id=%s as_of=%s universe=%s periods=%s",
            run_id, cutoff, len(universe), ",".join(item.value for item in periods),
        )
        for index, instrument in enumerate(universe, 1):
            try:
                analysis = self._analysis.analyze_screening_candidate(PatternAnalysisRequest(
                    symbol=instrument["symbol"],
                    timeframes=(AnalysisTimeframe.DAILY,), horizons=DEFAULT_HORIZONS,
                    config_version=CONFIG_VERSION, include_preview=False, as_of_date=cutoff,
                ), "major-descending", periods=tuple(item.value for item in periods),
                    states=tuple(item.value for item in states))
                if analysis is None:
                    raise LookupError("no matching shared structure")
                if analysis["status"] != "succeeded":
                    raise RuntimeError(f"pattern analysis failed: {instrument['symbol']}")
                lines = [
                    item for item in analysis["items"]
                    if item["item_type"] == "line"
                    and item["payload"].get("major_line_period") in period_set
                    and item["payload"].get("major_line_state") in state_set
                    and item["payload"].get("major_line_code")
                    and item["payload"].get("evolution_role") != "previous"
                ]
                if lines:
                    line = max(lines, key=lambda item: (
                        _state_priority(MajorLineState(item["payload"]["major_line_state"])),
                        item["payload"]["score"],
                    ))
                    payload = line["payload"]
                    # Numeric context only; structural decisions come exclusively
                    # from the saved analysis above, never a second detector pass.
                    analysis_input = self._inputs.build(
                        instrument["symbol"], cutoff, AnalysisTimeframe.DAILY,
                        AnalysisInputMode.FINAL, DEFAULT_HORIZONS,
                    )
                    evidence = {
                        **payload,
                        "period": payload["major_line_period"],
                        "state": payload["major_line_state"],
                        "first_date": payload["first_pivot_date"],
                        "second_date": payload["second_pivot_date"],
                        "latest_close": payload["projected_price"] * (1 + payload["distance_percent"] / 100),
                        **_local_structure(analysis_input.bars),
                        "as_of_date": cutoff.isoformat(),
                        **_scenario_evidence(analysis, line["item_id"]),
                    }
                    candidates.append({
                        "symbol": instrument["symbol"], "state": payload["major_line_state"],
                        "score": payload["score"], "line_item_id": line["item_id"],
                        "line_code": payload["major_line_code"], "analysis_run_id": analysis["run_id"],
                        "evidence": evidence,
                    })
            except (ValueError, LookupError) as error:
                LOGGER.debug(
                    "screener_symbol_skipped run_id=%s symbol=%s reason=%s",
                    run_id, instrument["symbol"], error,
                )
            if index % 100 == 0 or index == len(universe):
                self._store.update_screener_progress(
                    run_id, universe_count=len(universe), scanned_count=index
                )
                if index % 500 == 0 or index == len(universe):
                    LOGGER.info(
                        "screener_run_progress run_id=%s scanned=%s universe=%s matches=%s",
                        run_id, index, len(universe), len(candidates),
                    )
        candidates.sort(
            key=lambda item: (_state_priority(MajorLineState(item["state"])), item["score"]), reverse=True
        )
        retained = candidates[:max_results]
        self._store.complete_screener_run(run_id, retained, retention=10)
        LOGGER.info(
            "screener_run_completed run_id=%s as_of=%s universe=%s matches=%s retained=%s duration_ms=%.1f",
            run_id, cutoff, len(universe), len(candidates), len(retained),
            (time.perf_counter() - started) * 1000,
        )

    def _execute_first_pullback(self, run_id: str, cutoff: date, max_results: int,
                                strategy_id: str = PULLBACK_STRATEGY_ID) -> None:
        started = time.perf_counter()
        if strategy_id == DEEP_DRAWDOWN_STRATEGY_ID and cutoff < date.fromisoformat(REFERENCE_END):
            raise ValueError("deep-drawdown reference is only available from " + REFERENCE_END)
        universe = self._store.list_active_stock_symbols_for_screening()
        candidates = []
        self._store.update_screener_progress(run_id, universe_count=len(universe), scanned_count=0)
        for index, instrument in enumerate(universe, 1):
            try:
                if strategy_id == DEEP_DRAWDOWN_STRATEGY_ID and instrument["symbol"] == REFERENCE_SYMBOL:
                    raise LookupError("reference stock is excluded from similarity screening")
                request = PatternAnalysisRequest(
                    symbol=instrument["symbol"], timeframes=(AnalysisTimeframe.DAILY,),
                    horizons=DEFAULT_HORIZONS, config_version=_strategy_version(strategy_id),
                    include_preview=False, as_of_date=cutoff,
                )
                analysis = (
                    self._analysis.analyze_deep_drawdown_candidate(request)
                    if strategy_id == DEEP_DRAWDOWN_STRATEGY_ID
                    else self._analysis.analyze_low_base_candidate(request)
                    if strategy_id == LOW_BASE_STRATEGY_ID
                    else self._analysis.analyze_screening_candidate(
                        request, "bull-flag" if strategy_id == BULL_FLAG_STRATEGY_ID else "first-pullback",
                    )
                )
                if analysis is None:
                    raise LookupError("no matching shared structure")
                if analysis["status"] != "succeeded":
                    raise RuntimeError(f"pattern analysis failed: {instrument['symbol']}")
                for item in analysis["items"]:
                    evidence = item["payload"]
                    if (item["item_type"] != "zone"
                        or evidence.get("kind") != (DEEP_DRAWDOWN_KIND if strategy_id == DEEP_DRAWDOWN_STRATEGY_ID else BULL_FLAG_KIND if strategy_id == BULL_FLAG_STRATEGY_ID else "first-pullback-range")
                        or ((evidence.get("launch_type") == LOW_BASE_TYPE) != (strategy_id == LOW_BASE_STRATEGY_ID))
                        or not evidence.get("screen_eligible")
                        or evidence.get("stage") not in {"pullback-observation", "pullback-confirmed", DEEP_DRAWDOWN_STATE}
                        or evidence.get("as_of_date") != cutoff.isoformat()):
                        continue
                    candidates.append({
                        "symbol": instrument["symbol"], "state": evidence["stage"],
                        "score": evidence["score"], "line_item_id": item["item_id"],
                        "line_code": "DEEP-DRAWDOWN" if strategy_id == DEEP_DRAWDOWN_STRATEGY_ID else "BULL-FLAG" if strategy_id == BULL_FLAG_STRATEGY_ID else "LOW-BASE-PULLBACK" if strategy_id == LOW_BASE_STRATEGY_ID else "FIRST-PULLBACK",
                        "analysis_run_id": analysis["run_id"],
                        "evidence": evidence,
                    })
            except (ValueError, LookupError) as error:
                LOGGER.debug("screener_symbol_skipped run_id=%s symbol=%s reason=%s",
                             run_id, instrument["symbol"], error)
            if index % 10 == 0 or index == len(universe):
                self._store.update_screener_progress(run_id, universe_count=len(universe), scanned_count=index)
            if index % 100 == 0 or index == len(universe):
                LOGGER.info("screener_first_pullback_progress run_id=%s scanned=%s universe=%s matches=%s",
                            run_id, index, len(universe), len(candidates))
        candidates.sort(key=lambda item: (
            item["evidence"].get("maturity_rank", 3) if strategy_id == LOW_BASE_STRATEGY_ID else 0,
            strategy_id != LOW_BASE_STRATEGY_ID and item["state"] != "pullback-confirmed",
            -item["score"], item["symbol"],
        ))
        self._store.complete_screener_run(run_id, candidates[:max_results], retention=10)
        LOGGER.info("screener_first_pullback_completed run_id=%s matches=%s duration_ms=%.1f",
                    run_id, len(candidates), (time.perf_counter() - started) * 1000)

    def _execute_volume_accumulation(
        self, run_id: str, cutoff: date, max_results: int,
    ) -> None:
        started = time.perf_counter()
        universe = self._store.list_active_stock_symbols_for_screening()
        matches: list[tuple[dict[str, str], VolumeAccumulationSignal]] = []
        self._store.update_screener_progress(
            run_id, universe_count=len(universe), scanned_count=0,
        )
        LOGGER.info(
            "screener_accumulation_started run_id=%s as_of=%s universe=%s",
            run_id, cutoff, len(universe),
        )
        horizons = AnalysisHorizons(20, 60, ANALYSIS_LOOKBACK)
        for index, instrument in enumerate(universe, 1):
            try:
                analysis_input = self._inputs.build(
                    instrument["symbol"], cutoff, AnalysisTimeframe.DAILY,
                    AnalysisInputMode.FINAL, horizons,
                )
                recent = analysis_input.bars[-ANALYSIS_LOOKBACK:]
                if recent:
                    limit_dates = self._store.list_stock_limit_up_dates(
                        instrument["symbol"], recent[0].period_start, recent[-1].period_end,
                    )
                    signal = detect_volume_accumulation(
                        analysis_input.bars,
                        limit_up_dates=frozenset(item.isoformat() for item in limit_dates),
                    )
                    if signal is not None:
                        matches.append((instrument, signal))
            except (ValueError, LookupError) as error:
                LOGGER.debug(
                    "screener_symbol_skipped run_id=%s symbol=%s reason=%s",
                    run_id, instrument["symbol"], error,
                )
            if index % 100 == 0 or index == len(universe):
                self._store.update_screener_progress(
                    run_id, universe_count=len(universe), scanned_count=index,
                )
                if index % 500 == 0 or index == len(universe):
                    LOGGER.info(
                        "screener_accumulation_progress run_id=%s scanned=%s universe=%s matches=%s",
                        run_id, index, len(universe), len(matches),
                    )
        scan_finished = time.perf_counter()
        LOGGER.info(
            "screener_accumulation_scan_completed run_id=%s matches=%s duration_ms=%.1f",
            run_id, len(matches), (scan_finished - started) * 1000,
        )
        recognition = attach_recognition_tags(
            self._store, [{"symbol": item[0]["symbol"]} for item in matches], cutoff.isoformat(),
        )
        for (_, signal), tagged in zip(matches, recognition):
            signal.evidence["recognition"] = tagged["recognition"]
            signal.evidence["recognition_rank_bonus"] = 5.0 if tagged["recognition"]["tags"] else 0.0
        matches.sort(key=lambda item: accumulation_rank_key(item[1]), reverse=True)
        ranking_finished = time.perf_counter()
        LOGGER.info(
            "screener_accumulation_ranking_completed run_id=%s duration_ms=%.1f",
            run_id, (ranking_finished - scan_finished) * 1000,
        )
        retained: list[dict[str, object]] = []
        for instrument, signal in matches[:max_results]:
            analysis = self._analysis.analyze(PatternAnalysisRequest(
                symbol=instrument["symbol"], timeframes=(AnalysisTimeframe.DAILY,),
                horizons=DEFAULT_HORIZONS, config_version=CONFIG_VERSION,
                include_preview=False, as_of_date=cutoff,
            ))[0]
            representative = next(
                (item for item in analysis["items"]
                 if item.get("item_type") == "zone"
                 and item.get("payload", {}).get("kind") == "accumulation-range"),
                None,
            )
            item_id = str(representative["item_id"]) if representative else ""
            retained.append({
                "symbol": instrument["symbol"],
                "state": ACCUMULATION_STATE,
                "score": signal.score,
                "line_item_id": item_id,
                "line_code": "VOL-ACC-20D",
                "analysis_run_id": analysis["run_id"],
                "evidence": signal.evidence,
            })
        self._store.complete_screener_run(run_id, retained, retention=10)
        LOGGER.info(
            "screener_accumulation_analysis_completed run_id=%s retained=%s duration_ms=%.1f",
            run_id, len(retained), (time.perf_counter() - ranking_finished) * 1000,
        )
        LOGGER.info(
            "screener_accumulation_completed run_id=%s as_of=%s universe=%s matches=%s retained=%s duration_ms=%.1f",
            run_id, cutoff, len(universe), len(matches), len(retained),
            (time.perf_counter() - started) * 1000,
        )


def _strategy_version(strategy_id: str) -> str:
    if strategy_id == DEEP_DRAWDOWN_STRATEGY_ID:
        return DEEP_DRAWDOWN_VERSION
    if strategy_id == BULL_FLAG_STRATEGY_ID:
        return BULL_FLAG_VERSION
    if strategy_id == LOW_BASE_STRATEGY_ID:
        return LOW_BASE_VERSION
    if strategy_id == PULLBACK_STRATEGY_ID:
        return PULLBACK_STRATEGY_VERSION
    if strategy_id == STRATEGY_ID:
        return STRATEGY_VERSION
    if strategy_id == ACCUMULATION_STRATEGY_ID:
        return ACCUMULATION_STRATEGY_VERSION
    raise ValueError(f"unknown screener strategy: {strategy_id}")


def _parameters(strategy_id, periods, states, max_results: int) -> dict[str, object]:
    if strategy_id == DEEP_DRAWDOWN_STRATEGY_ID:
        return {"max_results": max_results, "final_bars_only": True, "window": 60,
                "states": [DEEP_DRAWDOWN_STATE], "minimum_score": MIN_SCORE,
                "reference_symbol": REFERENCE_SYMBOL, "reference_start": REFERENCE_START,
                "reference_end": REFERENCE_END, "analysis_config": DEEP_DRAWDOWN_VERSION}
    if strategy_id == BULL_FLAG_STRATEGY_ID:
        return {"max_results": max_results, "final_bars_only": True,
                "window": BULL_FLAG_CONFIG.max_launch_age, "states": ["pullback-observation"],
                "pattern_parameters": asdict(BULL_FLAG_CONFIG), "analysis_config": BULL_FLAG_VERSION}
    if strategy_id == LOW_BASE_STRATEGY_ID:
        return {"max_results": max_results, "final_bars_only": True,
                "window": LOW_BASE_CONFIG.observation_window_sessions,
                "states": ["pullback-observation", "pullback-confirmed"],
                "pattern_parameters": asdict(LOW_BASE_CONFIG), "analysis_config": LOW_BASE_VERSION}
    if strategy_id == PULLBACK_STRATEGY_ID:
        return {"max_results": max_results, "final_bars_only": True,
                "window": PULLBACK_CONFIG.observation_window_sessions,
                "states": ["pullback-observation", "pullback-confirmed"],
                "analysis_config": PULLBACK_STRATEGY_VERSION}
    if strategy_id == ACCUMULATION_STRATEGY_ID:
        return {
            "window": 20, "context_window": 80,
            "max_results": max_results, "final_bars_only": True,
        }
    return {
        "periods": [item.value for item in periods],
        "states": [item.value for item in states],
        "max_results": max_results, "final_bars_only": True,
    }


def _local_structure(bars) -> dict[str, object]:
    def view(count: int) -> dict[str, float]:
        selected = bars[-count:]
        midpoint = selected[len(selected) // 2]
        latest = selected[-1]
        return {
            "return_percent": round((latest.close / selected[0].close - 1) * 100, 4),
            "recent_half_percent": round((latest.close / midpoint.close - 1) * 100, 4),
            "low": min(item.low for item in selected), "high": max(item.high for item in selected),
        }
    return {"small_14": view(14), "medium_28": view(28)}


def _state_priority(state: MajorLineState) -> int:
    return {
        MajorLineState.BREAKOUT_RETEST: 3,
        MajorLineState.BROKEN_OUT: 2,
        MajorLineState.CRITICAL_BREAKOUT: 1,
    }[state]


def _scenario_evidence(
    analysis: dict[str, object], line_item_id: str,
) -> dict[str, object]:
    scenarios = [
        item for item in analysis.get("items", [])
        if isinstance(item, dict) and item.get("item_type") == "scenario"
        and isinstance(item.get("payload"), dict)
        and item["payload"].get("kind") == "structural-trade-scenario"
    ]
    scenarios.sort(key=lambda item: (
        line_item_id not in item["payload"].get("evidence_item_ids", []),
        not bool(item["payload"].get("primary")),
        int(item["payload"].get("rank") or 999),
    ))
    if not scenarios:
        return {
            "scenario_item_id": None, "invalidation_price": None,
            "first_target_price": None, "major_target_price": None,
            "first_risk_reward": None, "major_risk_reward": None,
            "trade_scenario": None,
        }
    scenario = scenarios[0]
    payload = scenario["payload"]
    targets = [
        target for target in payload.get("targets", []) if isinstance(target, dict)
    ]
    first = targets[0] if targets else None
    major = targets[-1] if targets else None
    return {
        "scenario_item_id": scenario["item_id"],
        "invalidation_price": payload.get("invalidation_price"),
        "first_target_price": first.get("price") if first else None,
        "major_target_price": major.get("price") if major else None,
        "first_risk_reward": first.get("risk_reward_ratio") if first else None,
        "major_risk_reward": major.get("risk_reward_ratio") if major else None,
        "trade_scenario": payload,
    }
