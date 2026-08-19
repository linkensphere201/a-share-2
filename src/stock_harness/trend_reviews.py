"""Persistence contracts for causal human review of trend-analysis output."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Mapping, Sequence


REVIEW_SCHEMA_VERSION = "1.0"
REVIEW_CLASSIFICATIONS = {"positive", "near-miss", "ambiguous", "robustness"}
REVIEW_TAGS = {
    "pivot", "trend-line", "key-level", "pattern", "breakout", "breakdown",
    "retest", "false-breakout", "corporate-action", "suspension", "gap",
}


class TrendReviewStatus(StrEnum):
    PROPOSED = "proposed"
    AMBIGUOUS = "ambiguous"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class TrendReviewDecision(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class TrendReviewLabel:
    item_id: str
    item_type: str
    decision: TrendReviewDecision
    payload: Mapping[str, Any]
    rationale: str = ""

    def validate(self) -> None:
        if not self.item_id.strip() or not self.item_type.strip():
            raise ValueError("trend review label item ID and type are required")
        if not isinstance(self.payload, Mapping):
            raise ValueError("trend review label payload must be an object")


@dataclass(frozen=True, slots=True)
class TrendReviewDraftSpec:
    symbol: str
    timeframe: str
    horizon: str
    interval_start: date
    interval_end: date
    as_of_date: date
    dataset_version: str
    classification: str
    status: TrendReviewStatus
    tags: Sequence[str]
    labels: Sequence[TrendReviewLabel]
    expected: Mapping[str, Any]
    rationale: str
    sources: Sequence[Mapping[str, str]]
    input_digest: bytes
    algorithm_version: str
    config_version: str
    settings: Mapping[str, Any]

    def validate(self) -> None:
        if not self.symbol.strip() or not self.dataset_version.strip():
            raise ValueError("trend review symbol and dataset version are required")
        if self.timeframe != "daily":
            raise ValueError("trend review currently supports daily timeframe only")
        if self.horizon not in {"short", "long"}:
            raise ValueError("trend review horizon must be short or long")
        if not self.interval_start <= self.interval_end <= self.as_of_date:
            raise ValueError("trend review interval must end on or before its as-of date")
        if self.classification not in REVIEW_CLASSIFICATIONS:
            raise ValueError("invalid trend review classification")
        if not self.tags or any(tag not in REVIEW_TAGS for tag in self.tags):
            raise ValueError("invalid trend review tags")
        if not self.input_digest or not self.algorithm_version.strip() or not self.config_version.strip():
            raise ValueError("trend review analytical identity is required")
        if not self.settings:
            raise ValueError("trend review settings snapshot is required")
        if not self.sources:
            raise ValueError("trend review provenance is required")
        ids = [label.item_id for label in self.labels]
        if len(ids) != len(set(ids)):
            raise ValueError("trend review label item IDs must be unique")
        for label in self.labels:
            label.validate()


def labels_from_analysis(
    items: Sequence[Mapping[str, object]],
    horizon: str,
) -> tuple[TrendReviewLabel, ...]:
    reviewable_types = {"anchor", "line", "zone", "pattern", "transition"}
    return tuple(
        TrendReviewLabel(
            item_id=str(item.get("item_id", "")),
            item_type=str(item.get("item_type", "")),
            decision=TrendReviewDecision.PENDING,
            payload=(item.get("payload") if isinstance(item.get("payload"), Mapping) else {}),
        )
        for item in items
        if str(item.get("item_type", "")) in reviewable_types
        and _belongs_to_horizon(item, horizon)
    )


def _belongs_to_horizon(item: Mapping[str, object], horizon: str) -> bool:
    payload = item.get("payload")
    if not isinstance(payload, Mapping):
        return True
    item_horizon = payload.get("horizon")
    if isinstance(item_horizon, str):
        return item_horizon == horizon
    parent = item.get("parent_item_id")
    if isinstance(parent, str) and parent.startswith(("short-", "long-")):
        return parent.startswith(f"{horizon}-")
    return True
