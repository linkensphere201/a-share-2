"""Versioned persistence contracts for generated trading-system analysis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Mapping, Sequence


class AnalysisNamespace(StrEnum):
    OFFICIAL = "official"
    PREVIEW = "preview"


class AnalysisRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class GeneratedItemType(StrEnum):
    ANCHOR = "anchor"
    LINE = "line"
    ZONE = "zone"
    PATTERN = "pattern"
    TRANSITION = "transition"
    EVIDENCE = "evidence"


@dataclass(frozen=True, slots=True)
class AnalysisRunSpec:
    system_id: str
    symbol: str
    timeframe: str
    namespace: AnalysisNamespace
    as_of_date: date
    input_start_date: date
    input_end_date: date
    input_digest: bytes
    algorithm_version: str
    config_version: str
    completion_state: str
    source_observed_at_ms: int | None = None
    expires_at_ms: int | None = None

    def validate(self) -> None:
        if not self.system_id.strip() or not self.symbol.strip() or not self.timeframe.strip():
            raise ValueError("analysis system, symbol, and timeframe are required")
        if self.input_start_date > self.input_end_date or self.input_end_date > self.as_of_date:
            raise ValueError("analysis input range must end on or before the as-of date")
        if not self.input_digest:
            raise ValueError("analysis input digest is required")
        if not self.algorithm_version.strip() or not self.config_version.strip():
            raise ValueError("analysis algorithm and config versions are required")
        if not self.completion_state.strip():
            raise ValueError("analysis completion state is required")
        if self.namespace is AnalysisNamespace.PREVIEW and self.source_observed_at_ms is None:
            raise ValueError("preview analysis requires a source observation time")


@dataclass(frozen=True, slots=True)
class GeneratedAnalysisItem:
    item_id: str
    item_type: GeneratedItemType
    payload: Mapping[str, Any]
    parent_item_id: str | None = None


@dataclass(frozen=True, slots=True)
class AnalysisRunRecord:
    run_id: str
    status: AnalysisRunStatus
    attempt: int
    supersedes_run_id: str | None
    reused: bool = False


@dataclass(frozen=True, slots=True)
class GeneratedAnalysisTarget:
    symbol: str
    system_id: str
    timeframe: str
    algorithm_version: str
    config_version: str
    enabled: bool = True

    def validate(self) -> None:
        values = (
            self.symbol, self.system_id, self.timeframe,
            self.algorithm_version, self.config_version,
        )
        if any(not value.strip() for value in values):
            raise ValueError("generated analysis target fields are required")


@dataclass(frozen=True, slots=True)
class ClaimedAnalysisTarget:
    target_id: int
    symbol: str
    system_id: str
    timeframe: str
    algorithm_version: str
    config_version: str
    dirty_from: date
    dirty_through: date
    reason: str
    generation: int


def validate_items(items: Sequence[GeneratedAnalysisItem]) -> None:
    ids = [item.item_id.strip() for item in items]
    if any(not item_id for item_id in ids):
        raise ValueError("generated analysis item ID is required")
    if len(ids) != len(set(ids)):
        raise ValueError("generated analysis item IDs must be unique within a run")
    seen: set[str] = set()
    for item in items:
        if item.parent_item_id is not None and item.parent_item_id not in seen:
            raise ValueError("generated analysis parent must precede its child in the same run")
        seen.add(item.item_id)
