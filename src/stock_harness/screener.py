"""Bounded asynchronous stock screening with immutable result snapshots."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
import logging
import threading
import time
from typing import Sequence
from uuid import uuid4

from stock_harness.analysis_inputs import (
    AnalysisHorizons, AnalysisInputMode, AnalysisInputService, AnalysisTimeframe,
)
from stock_harness.major_descending_lines import (
    MajorLinePeriod, MajorLineState,
)
from stock_harness.pattern_analysis import PatternAnalysisRequest, PatternAnalysisService
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.accumulation_pattern import ANALYSIS_LOOKBACK
from stock_harness.screener_result_tags import attach_recognition_tags
from stock_harness.screener_strategies import (
    get_strategy, strategy_definitions, STRATEGY_ID, STRATEGY_VERSION, CONFIG_VERSION,
    PULLBACK_STRATEGY_ID, PULLBACK_STRATEGY_VERSION, LOW_BASE_STRATEGY_ID,
    DEFAULT_HORIZONS, SCREENABLE_STATES,
    BULL_FLAG_STRATEGY_ID, DEEP_DRAWDOWN_STRATEGY_ID, PLATFORM_STRATEGY_ID,
)
from stock_harness.volume_accumulation import (
    STATE as ACCUMULATION_STATE,
    STRATEGY_ID as ACCUMULATION_STRATEGY_ID,
    VolumeAccumulationSignal,
    detect_volume_accumulation, accumulation_rank_key,
)


LOGGER = logging.getLogger(__name__)
class ScreenerBusyError(RuntimeError):
    pass


class ScreenerService:
    def __init__(self, store: SQLiteMarketDataStore) -> None:
        self._store = store
        self._inputs = AnalysisInputService(store)
        self._analysis = PatternAnalysisService(store)
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="stock-harness-screener")
        self._jobs: dict[str, tuple[str, str]] = {}
        self._stopping = threading.Event()
        recovered = store.recover_interrupted_screener_runs()
        if recovered:
            LOGGER.warning("screener_interrupted_runs_recovered count=%s", recovered)

    @staticmethod
    def strategies() -> list[dict[str, object]]:
        return strategy_definitions()

    def start_run(
        self,
        periods: Sequence[MajorLinePeriod],
        states: Sequence[MajorLineState],
        max_results: int,
        as_of_date: date | None = None,
        strategy_id: str = STRATEGY_ID,
    ) -> dict[str, object]:
        with self._lock:
            if self._stopping.is_set():
                raise ScreenerBusyError("screener is shutting down")
            if any(job[0] == strategy_id for job in self._jobs.values()):
                raise ScreenerBusyError("this strategy is already running or queued")
            cutoff = as_of_date or self._store.get_latest_stock_daily_bar_date()
            if cutoff is None:
                raise ValueError("no completed stock daily bars are available")
            run = self._store.create_screener_run(
                strategy_id, _strategy_version(strategy_id), cutoff,
                _parameters(strategy_id, periods, states, max_results),
            )
            run_id = str(run["run_id"])
            self._jobs[run_id] = (strategy_id, "queued")
            try:
                self._executor.submit(
                    self._run_guarded, run_id, cutoff, tuple(periods), tuple(states), max_results, strategy_id,
                )
            except Exception as error:
                self._jobs.pop(run_id, None)
                self._store.fail_screener_run(run_id, str(error))
                raise
        return self.describe_run(run)

    def describe_run(self, run: dict[str, object]) -> dict[str, object]:
        with self._lock:
            queued = [key for key, value in self._jobs.items() if value[1] == "queued"]
            job = self._jobs.get(str(run["run_id"]))
            if run["status"] == "running" and job is not None:
                return {**run, "execution_state": job[1],
                        "queue_position": queued.index(str(run["run_id"])) + 1 if job[1] == "queued" else 0}
        return run

    def start_all(self, max_results: int = 200, as_of_date: date | None = None) -> dict[str, object]:
        """Admit idle catalog strategies at one cutoff, then share compatible scans."""
        if not 1 <= max_results <= 500:
            raise ValueError("max_results must be between 1 and 500")
        runs, skipped = [], []
        batch_id = str(uuid4())
        periods = (MajorLinePeriod.HALF_YEAR, MajorLinePeriod.YEAR)
        with self._lock:
            if self._stopping.is_set():
                raise ScreenerBusyError("screener is shutting down")
            cutoff = as_of_date or self._store.get_latest_stock_daily_bar_date()
            if cutoff is None:
                raise ValueError("no completed stock daily bars are available")
            busy = {job[0] for job in self._jobs.values()}
            for definition in self.strategies():
                strategy_id = str(definition["strategy_id"])
                if strategy_id in busy:
                    skipped.append({"strategy_id": strategy_id, "reason": "already-running"})
                    continue
                strategy = get_strategy(strategy_id)
                try:
                    strategy.validate_cutoff(cutoff)
                except ValueError:
                    skipped.append({"strategy_id": strategy_id, "reason": "cutoff-unavailable"})
                    continue
                try:
                    run = self._store.create_screener_run(strategy_id, strategy.version, cutoff, {
                        **strategy.parameters(periods, SCREENABLE_STATES, max_results), "batch_id": batch_id,
                    })
                except Exception:
                    LOGGER.exception("screener_batch_admission_failed strategy=%s", strategy_id)
                    skipped.append({"strategy_id": strategy_id, "reason": "creation-failed"})
                    continue
                runs.append(run)
                self._jobs[str(run["run_id"])] = (strategy_id, "queued")
            shapes = [run for run in runs if get_strategy(str(run["strategy_id"])).execution == 'shape']
            groups = ([shapes] if shapes else []) + [[run] for run in runs if run not in shapes]
            for group in groups:
                try:
                    if get_strategy(str(group[0]["strategy_id"])).execution == 'shape':
                        self._executor.submit(self._run_shape_batch_guarded, group, cutoff, max_results)
                    else:
                        run = group[0]
                        self._executor.submit(self._run_guarded, str(run["run_id"]), cutoff,
                                              periods, SCREENABLE_STATES, max_results, str(run["strategy_id"]))
                except Exception as error:
                    for run in group:
                        self._jobs.pop(str(run["run_id"]), None)
                        self._store.fail_screener_run(str(run["run_id"]), str(error))
        return {"batch_id": batch_id, "as_of_date": cutoff,
                "items": [self.describe_run(self._store.get_screener_run(str(run["run_id"])) or run)
                          for run in runs], "skipped": skipped}

    def _run_shape_batch_guarded(self, runs: list[dict[str, object]], cutoff: date, max_results: int) -> None:
        try:
            with self._lock:
                for run in runs:
                    self._jobs[str(run["run_id"])] = (str(run["strategy_id"]), "running")
            self._check_stopping()
            self._execute_shape_batch(runs, cutoff, max_results)
        except Exception as error:
            LOGGER.exception("screener_shape_batch_failed")
            for run in runs:
                current = self._store.get_screener_run(str(run["run_id"]))
                if current and current["status"] == "running":
                    self._store.fail_screener_run(str(run["run_id"]), str(error))
        finally:
            with self._lock:
                for run in runs:
                    self._jobs.pop(str(run["run_id"]), None)

    def _execute_shape_batch(self, runs: list[dict[str, object]], cutoff: date, max_results: int) -> None:
        started = time.perf_counter()
        universe = self._store.list_active_stock_symbols_for_screening()
        active = {str(run["run_id"]): get_strategy(str(run["strategy_id"])) for run in runs}
        candidates = {run_id: [] for run_id in active}
        for run_id in active:
            self._store.update_screener_progress(run_id, universe_count=len(universe), scanned_count=0)
        for index, instrument in enumerate(universe, 1):
            self._check_stopping()
            if not active:
                break
            base = PatternAnalysisRequest(
                symbol=instrument["symbol"], timeframes=(AnalysisTimeframe.DAILY,),
                horizons=DEFAULT_HORIZONS, config_version="screener-batch-input-v1",
                include_preview=False, as_of_date=cutoff,
            )
            try:
                prepared = self._analysis.prepare_screening_subject(base)
            except (ValueError, LookupError):
                prepared = None
            if prepared is not None:
                for run_id, strategy in list(active.items()):
                    self._check_stopping()
                    if not strategy.allows_symbol(instrument["symbol"]):
                        continue
                    try:
                        analysis = strategy.analyze(self._analysis, replace(
                            base, config_version=strategy.analysis_config, prepared_input=prepared,
                        ))
                        candidates[run_id].extend(_shape_candidates(strategy, instrument, analysis, cutoff))
                    except (ValueError, LookupError) as error:
                        LOGGER.debug("screener_symbol_skipped run_id=%s symbol=%s reason=%s",
                                     run_id, instrument["symbol"], error)
                    except Exception as error:
                        LOGGER.exception("screener_batch_strategy_failed run_id=%s", run_id)
                        self._store.fail_screener_run(run_id, str(error))
                        active.pop(run_id)
            if index % 10 == 0 or index == len(universe):
                for run_id in active:
                    self._store.update_screener_progress(run_id, universe_count=len(universe), scanned_count=index)
        for run_id, strategy in active.items():
            self._check_stopping()
            try:
                candidates[run_id].sort(key=strategy.selection.rank_key)
                self._store.complete_screener_run(run_id, candidates[run_id][:max_results])
            except Exception as error:
                self._store.fail_screener_run(run_id, str(error))
                LOGGER.exception("screener_batch_completion_failed run_id=%s", run_id)
        LOGGER.info("screener_shape_batch_completed strategies=%s universe=%s duration_ms=%.1f",
                    len(runs), len(universe), (time.perf_counter() - started) * 1000)

    def close(self) -> None:
        with self._lock:
            self._stopping.set()
        # Drain accepted jobs before the application closes the shared SQLite store.
        self._executor.shutdown(wait=True)

    def _check_stopping(self) -> None:
        if self._stopping.is_set():
            raise RuntimeError("application stopped during screening")

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
            with self._lock:
                self._jobs[run_id] = (strategy_id, "running")
            self._check_stopping()
            self._execute(run_id, cutoff, periods, states, max_results, strategy_id)
        except Exception as error:
            self._store.fail_screener_run(run_id, str(error))
            LOGGER.exception("screener_run_failed run_id=%s", run_id)
        finally:
            with self._lock:
                self._jobs.pop(run_id, None)

    def _execute(
        self, run_id: str, cutoff: date, periods: Sequence[MajorLinePeriod],
        states: Sequence[MajorLineState], max_results: int, strategy_id: str,
    ) -> None:
        strategy = get_strategy(strategy_id)
        if strategy.execution == 'shape':
            self._execute_shape_strategy(run_id, cutoff, max_results, strategy_id)
            return
        if strategy.execution == 'accumulation':
            self._execute_volume_accumulation(run_id, cutoff, max_results)
            return
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
            self._check_stopping()
            try:
                analysis = self._analysis.analyze_screening_candidate(PatternAnalysisRequest(
                    symbol=instrument["symbol"],
                    timeframes=(AnalysisTimeframe.DAILY,), horizons=DEFAULT_HORIZONS,
                    config_version=get_strategy(STRATEGY_ID).analysis_config, include_preview=False, as_of_date=cutoff,
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
        self._store.complete_screener_run(run_id, retained)
        LOGGER.info(
            "screener_run_completed run_id=%s as_of=%s universe=%s matches=%s retained=%s duration_ms=%.1f",
            run_id, cutoff, len(universe), len(candidates), len(retained),
            (time.perf_counter() - started) * 1000,
        )

    def _execute_shape_strategy(self, run_id: str, cutoff: date, max_results: int,
                                strategy_id: str = PULLBACK_STRATEGY_ID) -> None:
        started = time.perf_counter()
        strategy = get_strategy(strategy_id)
        selection = strategy.selection
        if selection is None:
            raise ValueError(f"{strategy_id} has no saved shape selection")
        strategy.validate_cutoff(cutoff)
        universe = self._store.list_active_stock_symbols_for_screening()
        candidates = []
        self._store.update_screener_progress(run_id, universe_count=len(universe), scanned_count=0)
        for index, instrument in enumerate(universe, 1):
            self._check_stopping()
            try:
                if not strategy.allows_symbol(instrument["symbol"]):
                    raise LookupError("reference stock is excluded from similarity screening")
                request = PatternAnalysisRequest(
                    symbol=instrument["symbol"], timeframes=(AnalysisTimeframe.DAILY,),
                    horizons=DEFAULT_HORIZONS, config_version=strategy.analysis_config,
                    include_preview=False, as_of_date=cutoff,
                )
                analysis = strategy.analyze(self._analysis, request)
                candidates.extend(_shape_candidates(strategy, instrument, analysis, cutoff))
            except (ValueError, LookupError) as error:
                LOGGER.debug("screener_symbol_skipped run_id=%s symbol=%s reason=%s",
                             run_id, instrument["symbol"], error)
            if index % 10 == 0 or index == len(universe):
                self._store.update_screener_progress(run_id, universe_count=len(universe), scanned_count=index)
            if index % 100 == 0 or index == len(universe):
                LOGGER.info("screener_first_pullback_progress run_id=%s scanned=%s universe=%s matches=%s",
                            run_id, index, len(universe), len(candidates))
        candidates.sort(key=selection.rank_key)
        self._store.complete_screener_run(run_id, candidates[:max_results])
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
            self._check_stopping()
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
            self._check_stopping()
            analysis = self._analysis.analyze(PatternAnalysisRequest(
                symbol=instrument["symbol"], timeframes=(AnalysisTimeframe.DAILY,),
                horizons=DEFAULT_HORIZONS, config_version=get_strategy(ACCUMULATION_STRATEGY_ID).analysis_config,
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
        self._store.complete_screener_run(run_id, retained)
        LOGGER.info(
            "screener_accumulation_analysis_completed run_id=%s retained=%s duration_ms=%.1f",
            run_id, len(retained), (time.perf_counter() - ranking_finished) * 1000,
        )
        LOGGER.info(
            "screener_accumulation_completed run_id=%s as_of=%s universe=%s matches=%s retained=%s duration_ms=%.1f",
            run_id, cutoff, len(universe), len(matches), len(retained),
            (time.perf_counter() - started) * 1000,
        )


def _shape_candidates(strategy, instrument, analysis, cutoff):
    if analysis is None:
        return []
    if analysis["status"] != "succeeded":
        raise RuntimeError(f"pattern analysis failed: {instrument['symbol']}")
    return [{
        "symbol": instrument["symbol"], "state": item["payload"]["stage"],
        "score": item["payload"]["score"], "line_item_id": item["item_id"],
        "line_code": strategy.line_code, "analysis_run_id": analysis["run_id"],
        "evidence": item["payload"],
    } for item in analysis["items"] if strategy.selection.matches(item, cutoff)]


def _strategy_version(strategy_id: str) -> str:
    return get_strategy(strategy_id).version


def _parameters(strategy_id, periods, states, max_results: int) -> dict[str, object]:
    return get_strategy(strategy_id).parameters(periods, states, max_results)


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
