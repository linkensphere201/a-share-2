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
    MajorDescendingLine, MajorLineDiagnostics, MajorLinePeriod, MajorLineState,
    detect_major_descending_lines,
)
from stock_harness.pattern_analysis import PatternAnalysisRequest, PatternAnalysisService
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.volume_accumulation import (
    STATE as ACCUMULATION_STATE,
    STRATEGY_ID as ACCUMULATION_STRATEGY_ID,
    STRATEGY_VERSION as ACCUMULATION_STRATEGY_VERSION,
    VolumeAccumulationSignal,
    detect_volume_accumulation,
)


LOGGER = logging.getLogger(__name__)
STRATEGY_ID = "major-descending-breakout"
STRATEGY_VERSION = "major-descending-breakout-v4"
CONFIG_VERSION = "screener-major-descending-v4"
DEFAULT_HORIZONS = AnalysisHorizons(60, 120, 250)
SCREENABLE_STATES = (
    MajorLineState.CRITICAL_BREAKOUT,
    MajorLineState.BREAKOUT_RETEST,
    MajorLineState.BROKEN_OUT,
)
ACCUMULATION_EXCLUSION_LIMIT = 100
ACCUMULATION_LARGE_DROP_PERCENT = -7.0


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
            "baseline_window": 20,
            "states": [ACCUMULATION_STATE],
            "exclusion_pool_limit": ACCUMULATION_EXCLUSION_LIMIT,
            "large_drop_percent": ACCUMULATION_LARGE_DROP_PERCENT,
            "final_bars_only": True,
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
        diagnostics = MajorLineDiagnostics()
        candidates: list[tuple[dict[str, str], MajorDescendingLine, dict[str, object]]] = []
        self._store.update_screener_progress(
            run_id, universe_count=len(universe), scanned_count=0
        )
        LOGGER.info(
            "screener_run_started run_id=%s as_of=%s universe=%s periods=%s",
            run_id, cutoff, len(universe), ",".join(item.value for item in periods),
        )
        for index, instrument in enumerate(universe, 1):
            try:
                analysis_input = self._inputs.build(
                    instrument["symbol"], cutoff, AnalysisTimeframe.DAILY,
                    AnalysisInputMode.FINAL, DEFAULT_HORIZONS,
                )
                lines = [
                    item for item in detect_major_descending_lines(
                        analysis_input.bars, periods, diagnostics=diagnostics
                    )
                    if item.state in state_set
                ]
                if lines:
                    line = max(lines, key=lambda item: (_state_priority(item.state), item.score))
                    candidates.append((instrument, line, _local_structure(analysis_input.bars)))
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
            key=lambda item: (_state_priority(item[1].state), item[1].score), reverse=True
        )
        retained: list[dict[str, object]] = []
        for instrument, line, structure in candidates[:max_results]:
            analysis = self._analysis.analyze(PatternAnalysisRequest(
                symbol=instrument["symbol"],
                timeframes=(AnalysisTimeframe.DAILY,),
                horizons=DEFAULT_HORIZONS,
                config_version=CONFIG_VERSION, include_preview=False, as_of_date=cutoff,
            ))[0]
            if not any(item["item_id"] == line.item_id for item in analysis["items"]):
                raise RuntimeError(
                    f"screening line is absent from linked analysis: {instrument['symbol']} {line.item_id}"
                )
            evidence = asdict(line)
            evidence.update({
                "period": line.period.value, "state": line.state.value,
                "latest_close": line.projected_price * (1 + line.distance_percent / 100),
                "small_14": structure["small_14"],
                "medium_28": structure["medium_28"],
                "as_of_date": cutoff.isoformat(),
                **_scenario_evidence(analysis, line.item_id),
            })
            retained.append({
                "symbol": instrument["symbol"], "state": line.state.value,
                "score": line.score, "line_item_id": line.item_id,
                "line_code": line.code, "analysis_run_id": analysis["run_id"],
                "evidence": evidence,
            })
        self._store.complete_screener_run(run_id, retained, retention=10)
        LOGGER.info(
            "screener_run_completed run_id=%s as_of=%s universe=%s matches=%s retained=%s duration_ms=%.1f",
            run_id, cutoff, len(universe), len(candidates), len(retained),
            (time.perf_counter() - started) * 1000,
        )
        LOGGER.info(
            "screener_line_integrity run_id=%s pairs=%s accepted=%s "
            "wick_breach=%s body_breach=%s close_breach=%s dominant_high=%s "
            "missing_confirmation=%s",
            run_id, diagnostics.anchor_pairs, diagnostics.accepted,
            diagnostics.rejected_wick_breach,
            diagnostics.rejected_body_breach,
            diagnostics.rejected_close_breach,
            diagnostics.rejected_dominant_high,
            diagnostics.rejected_confirmation,
        )

    def _execute_volume_accumulation(
        self, run_id: str, cutoff: date, max_results: int,
    ) -> None:
        started = time.perf_counter()
        universe = self._store.list_active_stock_symbols_for_screening()
        matches: list[tuple[dict[str, str], VolumeAccumulationSignal]] = []
        exclusion_events: list[dict[str, object]] = []
        self._store.update_screener_progress(
            run_id, universe_count=len(universe), scanned_count=0,
        )
        LOGGER.info(
            "screener_accumulation_started run_id=%s as_of=%s universe=%s",
            run_id, cutoff, len(universe),
        )
        horizons = AnalysisHorizons(20, 40, 60)
        for index, instrument in enumerate(universe, 1):
            try:
                analysis_input = self._inputs.build(
                    instrument["symbol"], cutoff, AnalysisTimeframe.DAILY,
                    AnalysisInputMode.FINAL, horizons,
                )
                recent = analysis_input.bars[-20:]
                if len(recent) == 20:
                    limit_dates = self._store.list_stock_limit_up_dates(
                        instrument["symbol"], recent[0].period_start, recent[-1].period_end,
                    )
                    signal = detect_volume_accumulation(
                        analysis_input.bars,
                        limit_up_dates=frozenset(item.isoformat() for item in limit_dates),
                    )
                    if signal is not None:
                        matches.append((instrument, signal))
                    event = _latest_accumulation_exclusion_event(
                        instrument["symbol"], recent, limit_dates,
                    )
                    if event is not None:
                        exclusion_events.append(event)
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
        exclusion_pool = self._store.record_screener_exclusions(
            ACCUMULATION_STRATEGY_ID,
            exclusion_events,
            limit=ACCUMULATION_EXCLUSION_LIMIT,
            through_date=cutoff,
        )
        excluded_symbols = {str(item["symbol"]) for item in exclusion_pool}
        matches = [item for item in matches if item[0]["symbol"] not in excluded_symbols]
        matches.sort(key=lambda item: item[1].score, reverse=True)
        retained: list[dict[str, object]] = []
        for instrument, signal in matches[:max_results]:
            analysis = self._analysis.analyze(PatternAnalysisRequest(
                symbol=instrument["symbol"], timeframes=(AnalysisTimeframe.DAILY,),
                horizons=DEFAULT_HORIZONS, config_version=CONFIG_VERSION,
                include_preview=False, as_of_date=cutoff,
            ))[0]
            representative = next(
                (item for item in analysis["items"] if item.get("item_type") == "line"),
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
            "screener_accumulation_completed run_id=%s as_of=%s universe=%s matches=%s retained=%s exclusions=%s duration_ms=%.1f",
            run_id, cutoff, len(universe), len(matches), len(retained), len(exclusion_pool),
            (time.perf_counter() - started) * 1000,
        )


def _latest_accumulation_exclusion_event(symbol, bars, limit_dates):
    events: list[dict[str, object]] = []
    if limit_dates:
        event_date = max(limit_dates)
        events.append({
            "symbol": symbol,
            "event_date": event_date,
            "reason_code": "limit-up",
            "reason_text": "最近20日触及涨停",
            "evidence": {"event_date": event_date.isoformat()},
        })
    for index in range(1, len(bars)):
        change = (bars[index].close / bars[index - 1].close - 1) * 100
        if change <= ACCUMULATION_LARGE_DROP_PERCENT:
            events.append({
                "symbol": symbol,
                "event_date": bars[index].period_end,
                "reason_code": "large-drop",
                "reason_text": f"单日收盘下跌 {abs(change):.2f}%",
                "evidence": {
                    "event_date": bars[index].period_end.isoformat(),
                    "change_percent": round(change, 4),
                    "threshold_percent": ACCUMULATION_LARGE_DROP_PERCENT,
                },
            })
    return max(events, key=lambda item: item["event_date"]) if events else None


def _strategy_version(strategy_id: str) -> str:
    if strategy_id == STRATEGY_ID:
        return STRATEGY_VERSION
    if strategy_id == ACCUMULATION_STRATEGY_ID:
        return ACCUMULATION_STRATEGY_VERSION
    raise ValueError(f"unknown screener strategy: {strategy_id}")


def _parameters(strategy_id, periods, states, max_results: int) -> dict[str, object]:
    if strategy_id == ACCUMULATION_STRATEGY_ID:
        return {
            "window": 20, "baseline_window": 20,
            "exclusion_pool_limit": ACCUMULATION_EXCLUSION_LIMIT,
            "large_drop_percent": ACCUMULATION_LARGE_DROP_PERCENT,
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
