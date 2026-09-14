"""System-neutral orchestration for frozen-signal historical replay."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from typing import Protocol

from stock_harness.models import StoredDailyBar
from stock_harness.replay.contracts import DEFAULT_EVALUATION_HORIZONS, FrozenSignal
from stock_harness.replay.outcomes import (
    evaluate_frozen_signal,
    summarize_evaluations,
    summarize_evaluations_by,
)
from stock_harness.replay.observations import observation_events


class ReplaySystemAdapter(Protocol):
    def generate(self, cutoffs: Sequence[date]) -> Iterable[FrozenSignal]: ...


class ReplayFutureDataSource(Protocol):
    def future_bars(self, signal: FrozenSignal, sessions: int) -> Sequence[StoredDailyBar]: ...


class AnalysisReplayEngine:
    """Evaluate any analysis system through the same frozen outcome contract."""

    def __init__(
        self, future_data: ReplayFutureDataSource,
        *, horizons: Sequence[int] = DEFAULT_EVALUATION_HORIZONS,
    ) -> None:
        self._future_data = future_data
        self._horizons = tuple(horizons)

    def run(
        self, adapter: ReplaySystemAdapter, cutoffs: Sequence[date],
    ) -> dict[str, object]:
        signals = list(adapter.generate(tuple(cutoffs)))
        evaluations = [
            evaluate_frozen_signal(
                signal,
                self._future_data.future_bars(signal, max(
                    max(self._horizons),
                    signal.observation_sessions or 0,
                    (plan.maximum_holding_sessions or 0)
                    if (plan := signal.resolved_exit_plan()) else 0,
                )),
                horizons=self._horizons,
            )
            for signal in signals
        ]
        summaries = {}
        for basis in ("reference_close", "next_open"):
            summaries[basis] = {
                "overall": summarize_evaluations(
                    evaluations, horizons=self._horizons, basis=basis,
                ),
                "by_scope": summarize_evaluations_by(
                    evaluations, "scope", horizons=self._horizons, basis=basis,
                ),
                "by_setup_family": summarize_evaluations_by(
                    evaluations, "setup_family", horizons=self._horizons, basis=basis,
                ),
            }
        return {
            "cutoffs": [value.isoformat() for value in cutoffs],
            "signals": [signal.to_dict() for signal in signals],
            "evaluations": evaluations,
            "observation_events": observation_events(evaluations),
            "summaries": summaries,
        }
