"""Shared causal replay and outcome evaluation infrastructure."""

from stock_harness.replay.contracts import (
    DEFAULT_EVALUATION_HORIZONS,
    EVALUATION_CONTRACT_VERSION,
    FrozenSignal,
)
from stock_harness.replay.engine import AnalysisReplayEngine, ReplaySystemAdapter
from stock_harness.replay.outcomes import (
    evaluate_frozen_signal,
    summarize_evaluations,
    summarize_evaluations_by,
)

__all__ = [
    "AnalysisReplayEngine",
    "DEFAULT_EVALUATION_HORIZONS",
    "EVALUATION_CONTRACT_VERSION",
    "FrozenSignal",
    "ReplaySystemAdapter",
    "evaluate_frozen_signal",
    "summarize_evaluations",
    "summarize_evaluations_by",
]
