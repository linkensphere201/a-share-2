"""Shared causal replay and outcome evaluation infrastructure."""

from stock_harness.replay.contracts import (
    DEFAULT_EVALUATION_HORIZONS,
    EVALUATION_CONTRACT_VERSION,
    FrozenSignal,
    ExitPlan,
)
from stock_harness.replay.cases import CASE_LIBRARY_VERSION, select_replay_cases
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
    "ExitPlan",
    "CASE_LIBRARY_VERSION",
    "select_replay_cases",
    "ReplaySystemAdapter",
    "evaluate_frozen_signal",
    "summarize_evaluations",
    "summarize_evaluations_by",
]
