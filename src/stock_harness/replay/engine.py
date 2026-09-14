"""System-neutral orchestration for frozen-signal historical replay."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from typing import Protocol

from stock_harness.models import StoredDailyBar
from stock_harness.replay.contracts import (
    DEFAULT_EVALUATION_HORIZONS, EVALUATION_CONTRACT_VERSION, FrozenSignal,
)
from stock_harness.replay.outcomes import (
    evaluate_frozen_signal,
    summarize_evaluations,
    summarize_evaluations_by,
)
from stock_harness.replay.observations import observation_events


class ReplaySystemAdapter(Protocol):
    def generate(self, cutoffs: Sequence[date]) -> Iterable[FrozenSignal]: ...


class ReplayFutureDataSource(Protocol):
    def future_bars(self, signal: FrozenSignal, sessions: int | None) -> Sequence[StoredDailyBar]:
        """Return bars through the source cutoff; None requests all available sessions."""
        ...


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
                self._future_data.future_bars(signal, None),
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
            "contract_version": EVALUATION_CONTRACT_VERSION,
            "primary_metric": "simulated-exit-net-return",
            "primary_basis": "next_open",
            "execution_model": "daily-ohlc-stop-first-ideal-fill-v1",
            "performance_role": "independent-claim-simulation-not-portfolio-or-broker-fills",
            "cutoffs": [value.isoformat() for value in cutoffs],
            "signals": [signal.to_dict() for signal in signals],
            "evaluations": evaluations,
            "observation_events": observation_events(evaluations),
            "summaries": summaries,
        }
