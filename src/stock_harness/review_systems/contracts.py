"""Trait-object contract for complete deterministic review systems."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol


ANALYSIS_RESULT_CONTRACT_VERSION = "review-analysis-result-v1"
SUPPORTED_SCOPES = frozenset({"market", "board", "stock"})


@dataclass(frozen=True, slots=True)
class AnalysisSystemDefinition:
    system_id: str
    version: str
    display_name: str
    supported_scopes: tuple[str, ...]
    setup_families: tuple[str, ...]
    timeframes: tuple[str, ...] = ("daily",)
    dependencies: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = (
        "scorecard", "conclusion", "evidence", "opportunity",
        "chart-projection", "history", "comparison", "replay",
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "system_id": self.system_id, "version": self.version,
            "display_name": self.display_name,
            "supported_scopes": list(self.supported_scopes),
            "setup_families": list(self.setup_families),
            "timeframes": list(self.timeframes),
            "dependencies": list(self.dependencies),
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True, slots=True)
class AnalysisSystemContext:
    entities_by_scope: Mapping[str, Sequence[Mapping[str, object]]]
    prior_results: Mapping[str, Mapping[str, Mapping[str, object]]] = field(
        default_factory=dict
    )
    recent_results: Mapping[
        str, Mapping[str, Sequence[Mapping[str, object]]]
    ] = field(default_factory=dict)
    dependencies: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AnalysisSystemExecution:
    system_id: str
    version: str
    results: tuple[dict[str, object], ...]
    elapsed_ms: float
    error: str | None = None


class ReviewAnalysisSystem(Protocol):
    definition: AnalysisSystemDefinition

    def analyze(self, context: AnalysisSystemContext) -> Sequence[dict[str, object]]: ...

    def replay_definition(self) -> Mapping[str, object]: ...


def validate_system_definition(definition: AnalysisSystemDefinition) -> None:
    if not definition.system_id.strip() or not definition.version.strip():
        raise ValueError("analysis system id and version are required")
    if not definition.display_name.strip():
        raise ValueError("analysis system display name is required")
    if not definition.supported_scopes or any(
        scope not in SUPPORTED_SCOPES for scope in definition.supported_scopes
    ):
        raise ValueError("analysis system has unsupported entity scopes")
    if len(definition.setup_families) != len(set(definition.setup_families)):
        raise ValueError("analysis system setup families must be unique")
    if not definition.setup_families or any(not value for value in definition.setup_families):
        raise ValueError("analysis system setup families are required")
    required = {"scorecard", "conclusion", "evidence", "opportunity", "chart-projection"}
    if not required.issubset(definition.capabilities):
        raise ValueError("analysis system is missing mandatory capabilities")


def validate_analysis_result(
    value: Mapping[str, object], definition: AnalysisSystemDefinition,
) -> None:
    required = {
        "contract_version", "system_id", "system_version", "setup_family",
        "timeframe", "entity_scope", "entity_key", "symbol", "eligibility",
        "scorecard", "conclusion", "evidence", "opportunity",
        "chart_projection", "diagnostics", "system_payload",
    }
    missing = sorted(required - value.keys())
    if missing:
        raise ValueError("analysis result missing fields: " + ", ".join(missing))
    if value["contract_version"] != ANALYSIS_RESULT_CONTRACT_VERSION:
        raise ValueError("unsupported analysis result contract")
    if value["system_id"] != definition.system_id or value["system_version"] != definition.version:
        raise ValueError("analysis result system identity mismatch")
    if value["setup_family"] not in definition.setup_families:
        raise ValueError("analysis result setup family is not declared")
    if value["entity_scope"] not in definition.supported_scopes:
        raise ValueError("analysis result entity scope is not declared")
    for key in ("eligibility", "scorecard", "conclusion", "opportunity", "diagnostics"):
        if not isinstance(value[key], Mapping):
            raise ValueError(f"analysis result {key} must be an object")
    if not isinstance(value["evidence"], Sequence) or isinstance(value["evidence"], (str, bytes)):
        raise ValueError("analysis result evidence must be a list")
    if not isinstance(value["chart_projection"], Sequence) or isinstance(
        value["chart_projection"], (str, bytes)
    ):
        raise ValueError("analysis result chart projection must be a list")
    score = value["scorecard"].get("total_score")  # type: ignore[union-attr]
    if not isinstance(score, (int, float)) or not 0 <= float(score) <= 100:
        raise ValueError("analysis result score must be between 0 and 100")
