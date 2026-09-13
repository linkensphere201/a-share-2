"""Complete, versioned analysis-system plug-ins for signal review."""

from stock_harness.review_systems.contracts import (
    ANALYSIS_RESULT_CONTRACT_VERSION,
    AnalysisSystemContext,
    AnalysisSystemDefinition,
    AnalysisSystemExecution,
    ReviewAnalysisSystem,
    validate_analysis_result,
    validate_system_definition,
)
from stock_harness.review_systems.mean_reversion import (
    MEAN_REVERSION_SYSTEM_ID,
    MeanReversionReviewSystem,
)
from stock_harness.review_systems.registry import ReviewAnalysisSystemRegistry
from stock_harness.review_systems.scorer_adapter import ScorerAnalysisSystemAdapter

__all__ = [
    "ANALYSIS_RESULT_CONTRACT_VERSION",
    "AnalysisSystemContext",
    "AnalysisSystemDefinition",
    "AnalysisSystemExecution",
    "MEAN_REVERSION_SYSTEM_ID",
    "MeanReversionReviewSystem",
    "ReviewAnalysisSystem",
    "ReviewAnalysisSystemRegistry",
    "ScorerAnalysisSystemAdapter",
    "validate_analysis_result",
    "validate_system_definition",
]
