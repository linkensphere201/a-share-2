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
    MajorDescendingLine, MajorLinePeriod, MajorLineState,
    detect_major_descending_lines,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.trend_analysis import TrendAnalysisService


LOGGER = logging.getLogger(__name__)
STRATEGY_ID = "major-descending-breakout"
STRATEGY_VERSION = "major-descending-breakout-v1"
CONFIG_VERSION = "screener-major-descending-v1"
DEFAULT_HORIZONS = AnalysisHorizons(60, 120, 250)


class ScreenerBusyError(RuntimeError):
    pass


class ScreenerService:
    def __init__(self, store: SQLiteMarketDataStore) -> None:
        self._store = store
        self._inputs = AnalysisInputService(store)
        self._analysis = TrendAnalysisService(store)
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
            "periods": [item.value for item in MajorLinePeriod],
            "states": [item.value for item in MajorLineState],
            "final_bars_only": True,
        }]

    def start_run(
        self,
        periods: Sequence[MajorLinePeriod],
        states: Sequence[MajorLineState],
        max_results: int,
        as_of_date: date | None = None,
    ) -> dict[str, object]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ScreenerBusyError("another screener run is already active")
            cutoff = as_of_date or self._store.get_latest_stock_daily_bar_date()
            if cutoff is None:
                raise ValueError("no completed stock daily bars are available")
            run = self._store.create_screener_run(
                STRATEGY_ID, STRATEGY_VERSION, cutoff,
                _parameters(periods, states, max_results),
            )
            self._thread = threading.Thread(
                target=self._run_guarded,
                args=(str(run["run_id"]), cutoff, tuple(periods), tuple(states), max_results),
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
    ) -> dict[str, object]:
        run = self._store.create_screener_run(
            STRATEGY_ID, STRATEGY_VERSION, as_of_date,
            _parameters(periods, states, max_results),
        )
        try:
            self._execute(str(run["run_id"]), as_of_date, periods, states, max_results)
        except Exception as error:
            self._store.fail_screener_run(str(run["run_id"]), str(error))
            raise
        return self._store.get_screener_run(str(run["run_id"]))  # type: ignore[return-value]

    def _run_guarded(
        self, run_id: str, cutoff: date, periods: Sequence[MajorLinePeriod],
        states: Sequence[MajorLineState], max_results: int,
    ) -> None:
        try:
            self._execute(run_id, cutoff, periods, states, max_results)
        except Exception as error:
            self._store.fail_screener_run(run_id, str(error))
            LOGGER.exception("screener_run_failed run_id=%s", run_id)

    def _execute(
        self, run_id: str, cutoff: date, periods: Sequence[MajorLinePeriod],
        states: Sequence[MajorLineState], max_results: int,
    ) -> None:
        started = time.perf_counter()
        universe = self._store.list_active_stock_symbols_for_screening()
        state_set = set(states)
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
                    item for item in detect_major_descending_lines(analysis_input.bars, periods)
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
            analysis = self._analysis.recalculate(
                instrument["symbol"], [AnalysisTimeframe.DAILY], DEFAULT_HORIZONS,
                config_version=CONFIG_VERSION, include_preview=False, as_of_date=cutoff,
            )[0]
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


def _parameters(periods, states, max_results: int) -> dict[str, object]:
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
