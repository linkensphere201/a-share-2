"""Versioned human-review contracts for A-share trend-analysis validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import re
from typing import Any, Mapping


SCHEMA_VERSION = "1.0"
_SYMBOL = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
_CLASSIFICATIONS = {"positive", "near-miss", "ambiguous", "robustness"}
_STATUSES = {"proposed", "ambiguous", "confirmed", "rejected"}
_TAGS = {
    "pivot", "trend-line", "key-level", "pattern", "breakout", "breakdown",
    "retest", "false-breakout", "corporate-action", "suspension", "gap",
}
_SCOREABLE_EXPECTED_FIELDS = {
    "pivots", "trend_lines", "key_levels", "patterns", "events", "forbidden",
}


def has_scoreable_expected_labels(expected: Mapping[str, Any]) -> bool:
    return any(
        isinstance(expected.get(field), (list, dict))
        and bool(expected.get(field))
        for field in _SCOREABLE_EXPECTED_FIELDS
    )


@dataclass(frozen=True, slots=True)
class TrendReviewCase:
    case_id: str
    symbol: str
    name: str
    timeframe: str
    horizon: str
    interval_start: date
    interval_end: date
    as_of_date: date
    classification: str
    review_status: str
    tags: tuple[str, ...]
    expected: Mapping[str, Any]
    rationale: str
    sources: tuple[Mapping[str, str], ...]

    @property
    def scoreable(self) -> bool:
        return self.review_status == "confirmed"


@dataclass(frozen=True, slots=True)
class TrendReviewSet:
    schema_version: str
    dataset_version: str
    cases: tuple[TrendReviewCase, ...]

    @property
    def scoreable_cases(self) -> tuple[TrendReviewCase, ...]:
        return tuple(case for case in self.cases if case.scoreable)


def load_trend_review_set(path: Path) -> TrendReviewSet:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("trend review set must be a JSON object")
    schema_version = _required_text(raw, "schema_version")
    if schema_version != SCHEMA_VERSION:
        raise ValueError(f"unsupported trend review schema: {schema_version}")
    dataset_version = _required_text(raw, "dataset_version")
    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("trend review cases must be a list")
    cases = tuple(_parse_case(item) for item in raw_cases)
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("trend review case IDs must be unique")
    return TrendReviewSet(schema_version, dataset_version, cases)


def _parse_case(raw: object) -> TrendReviewCase:
    if not isinstance(raw, dict):
        raise ValueError("trend review case must be an object")
    case_id = _required_text(raw, "case_id")
    symbol = _required_text(raw, "symbol").upper()
    if _SYMBOL.fullmatch(symbol) is None:
        raise ValueError(f"invalid A-share review symbol: {symbol}")
    timeframe = _required_text(raw, "timeframe")
    if timeframe != "daily":
        raise ValueError("M4 trend review currently supports daily cases only")
    horizon = _required_text(raw, "horizon")
    if horizon not in {"short", "long"}:
        raise ValueError(f"invalid review horizon: {horizon}")
    classification = _required_text(raw, "classification")
    if classification not in _CLASSIFICATIONS:
        raise ValueError(f"invalid review classification: {classification}")
    review_status = _required_text(raw, "review_status")
    if review_status not in _STATUSES:
        raise ValueError(f"invalid review status: {review_status}")
    start = _required_date(raw, "interval_start")
    end = _required_date(raw, "interval_end")
    as_of = _required_date(raw, "as_of_date")
    if not start <= end <= as_of:
        raise ValueError(f"invalid review interval for {case_id}")
    tags = raw.get("tags")
    if (
        not isinstance(tags, list)
        or not tags
        or any(not isinstance(tag, str) or tag not in _TAGS for tag in tags)
    ):
        raise ValueError(f"invalid review tags for {case_id}")
    expected = raw.get("expected")
    if not isinstance(expected, dict) or not any(expected.values()):
        raise ValueError(f"review case {case_id} requires expected evidence")
    if (
        review_status == "confirmed"
        and not has_scoreable_expected_labels(expected)
    ):
        raise ValueError(
            f"confirmed review case {case_id} requires scoreable expected labels"
        )
    sources = raw.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError(f"review case {case_id} requires provenance")
    parsed_sources: list[Mapping[str, str]] = []
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError(f"invalid review source for {case_id}")
        checked_on = _required_date(source, "checked_on")
        parsed_sources.append({
            "provider": _required_text(source, "provider"),
            "dataset": _required_text(source, "dataset"),
            "checked_on": checked_on.isoformat(),
        })
    return TrendReviewCase(
        case_id, symbol, _required_text(raw, "name"), timeframe, horizon,
        start, end, as_of, classification, review_status,
        tuple(dict.fromkeys(tags)), expected,
        _required_text(raw, "rationale"), tuple(parsed_sources),
    )


def _required_text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"trend review field {key} is required")
    return value.strip()


def _required_date(raw: Mapping[str, object], key: str) -> date:
    value = _required_text(raw, key)
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"invalid trend review date {key}: {value}") from error
