"""Production-equivalent historical replay for causal trend analysis."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from typing import Any

from stock_harness.analysis_inputs import AnalysisHorizons, AnalysisTimeframe
from stock_harness.trend_analysis import TrendAnalysisService


@dataclass(frozen=True, slots=True)
class ReplayItem:
    item_id: str
    item_type: str
    parent_item_id: str | None
    payload_json: str


@dataclass(frozen=True, slots=True)
class TrendReplaySnapshot:
    as_of_date: date
    timeframe: AnalysisTimeframe
    input_digest: str
    output_digest: str
    completion_state: str
    item_counts: tuple[tuple[str, int], ...]
    structural_events: tuple[str, ...]
    warnings_json: str
    items: tuple[ReplayItem, ...]


@dataclass(frozen=True, slots=True)
class ReplayMismatch:
    as_of_date: date
    timeframe: AnalysisTimeframe
    expected_digest: str
    actual_digest: str


@dataclass(frozen=True, slots=True)
class TrendReplayMetrics:
    snapshot_count: int
    unique_anchor_count: int
    anchor_confirmation_lag_mean_days: float | None
    anchor_confirmation_lag_max_days: int | None
    unique_pattern_count: int
    pattern_availability_lag_mean_days: float | None
    pattern_availability_lag_max_days: int | None
    candidate_stability_mean: float
    alert_turnover_rate: float
    event_counts: tuple[tuple[str, int], ...]


def replay_trend_analysis(
    service: TrendAnalysisService,
    symbol: str,
    cutoffs: Sequence[date],
    *,
    timeframe: AnalysisTimeframe = AnalysisTimeframe.DAILY,
    horizons: AnalysisHorizons = AnalysisHorizons(60, 120, 250),
    config_version: str = "historical-replay-v1",
) -> tuple[TrendReplaySnapshot, ...]:
    """Run the production coordinator at each cutoff before revealing the next bar."""
    ordered = tuple(cutoffs)
    if not ordered:
        raise ValueError("historical replay requires at least one cutoff")
    if any(left >= right for left, right in zip(ordered, ordered[1:])):
        raise ValueError("historical replay cutoffs must be strictly chronological")
    snapshots: list[TrendReplaySnapshot] = []
    for cutoff in ordered:
        result = service.recalculate(
            symbol,
            [timeframe],
            horizons,
            config_version=config_version,
            include_preview=False,
            as_of_date=cutoff,
        )[0]
        snapshots.append(_snapshot_result(result, timeframe))
    return tuple(snapshots)


def compare_replays(
    expected: Sequence[TrendReplaySnapshot],
    actual: Sequence[TrendReplaySnapshot],
) -> tuple[ReplayMismatch, ...]:
    """Compare analytical output only; runtime IDs and timings are intentionally absent."""
    expected_by_key = {(item.as_of_date, item.timeframe): item for item in expected}
    actual_by_key = {(item.as_of_date, item.timeframe): item for item in actual}
    if expected_by_key.keys() != actual_by_key.keys():
        raise ValueError("historical replay cutoffs and timeframes must match")
    return tuple(
        ReplayMismatch(key[0], key[1], expected_by_key[key].output_digest, actual_by_key[key].output_digest)
        for key in sorted(expected_by_key, key=lambda value: (value[0], value[1].value))
        if expected_by_key[key].output_digest != actual_by_key[key].output_digest
    )


def summarize_replay(
    snapshots: Sequence[TrendReplaySnapshot],
) -> TrendReplayMetrics:
    """Measure recognition lag and revision stability without using future returns."""
    if not snapshots:
        raise ValueError("replay metrics require at least one snapshot")
    anchors: dict[tuple[object, ...], int] = {}
    patterns: dict[tuple[object, ...], int] = {}
    candidate_sets: list[set[tuple[object, ...]]] = []
    alert_sets: list[set[tuple[object, ...]]] = []
    event_counts: Counter[str] = Counter()
    for snapshot in snapshots:
        candidates: set[tuple[object, ...]] = set()
        parent_candidates: dict[str, tuple[object, ...]] = {}
        decoded_items = [
            (item, json.loads(item.payload_json)) for item in snapshot.items
        ]
        for item, payload in decoded_items:
            identity = _candidate_identity(item.item_type, payload)
            if identity is not None:
                candidates.add(identity)
                parent_candidates[item.item_id] = identity
        alerts: set[tuple[object, ...]] = set()
        event_counts.update(snapshot.structural_events)
        for item, payload in decoded_items:
            if item.item_type == "transition":
                event_kind = payload.get("event_kind")
                if event_kind != "no-structural-change":
                    alerts.add((
                        event_kind,
                        parent_candidates.get(
                            item.parent_item_id or "",
                            ("unresolved-parent", item.parent_item_id),
                        ),
                        payload.get("direction"),
                    ))
            if item.item_type == "anchor" and payload.get("tentative") is False:
                pivot_date = _parse_date(payload.get("pivot_date"))
                confirmed_date = _parse_date(payload.get("confirmed_date"))
                if pivot_date is not None and confirmed_date is not None:
                    key = (
                        payload.get("horizon"), payload.get("kind"),
                        pivot_date, confirmed_date,
                    )
                    anchors[key] = (confirmed_date - pivot_date).days
            if item.item_type == "pattern":
                end_date = _parse_date(payload.get("end_date"))
                available_date = _parse_date(payload.get("available_date"))
                if end_date is not None and available_date is not None:
                    key = (
                        payload.get("horizon"), payload.get("pattern_type"),
                        payload.get("start_date"), end_date, available_date,
                    )
                    patterns[key] = max(0, (available_date - end_date).days)
        candidate_sets.append(candidates)
        alert_sets.append(alerts)
    stability = [
        _jaccard(left, right)
        for left, right in zip(candidate_sets, candidate_sets[1:])
    ]
    alert_turns = sum(
        left != right for left, right in zip(alert_sets, alert_sets[1:])
    )
    transition_count = max(0, len(snapshots) - 1)
    return TrendReplayMetrics(
        snapshot_count=len(snapshots),
        unique_anchor_count=len(anchors),
        anchor_confirmation_lag_mean_days=_mean_or_none(anchors.values()),
        anchor_confirmation_lag_max_days=max(anchors.values(), default=None),
        unique_pattern_count=len(patterns),
        pattern_availability_lag_mean_days=_mean_or_none(patterns.values()),
        pattern_availability_lag_max_days=max(patterns.values(), default=None),
        candidate_stability_mean=(
            sum(stability) / len(stability) if stability else 1.0
        ),
        alert_turnover_rate=(
            alert_turns / transition_count if transition_count else 0.0
        ),
        event_counts=tuple(sorted(event_counts.items())),
    )


def _snapshot_result(
    result: Mapping[str, object],
    timeframe: AnalysisTimeframe,
) -> TrendReplaySnapshot:
    as_of = result.get("as_of_date")
    if not isinstance(as_of, date):
        raise ValueError("analysis result is missing its as-of date")
    raw_items = result.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("analysis result is missing generated items")
    items: list[ReplayItem] = []
    counts: Counter[str] = Counter()
    events: list[str] = []
    canonical_items: list[dict[str, object]] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            raise ValueError("analysis result contains an invalid generated item")
        item_id = str(raw_item.get("item_id", ""))
        item_type = str(raw_item.get("item_type", ""))
        parent = raw_item.get("parent_item_id")
        payload = _normalize(raw_item.get("payload", {}))
        payload_json = _canonical_json(payload)
        items.append(ReplayItem(
            item_id,
            item_type,
            str(parent) if parent is not None else None,
            payload_json,
        ))
        counts[item_type] += 1
        if item_type == "transition" and isinstance(payload, dict):
            event_kind = payload.get("event_kind")
            if isinstance(event_kind, str):
                events.append(event_kind)
        canonical_items.append({
            "item_id": item_id,
            "item_type": item_type,
            "parent_item_id": parent,
            "payload": payload,
        })
    warnings = _normalize(result.get("warnings", []))
    warnings_json = _canonical_json(warnings)
    input_digest = result.get("input_digest")
    if isinstance(input_digest, bytes):
        input_digest = input_digest.hex()
    if not isinstance(input_digest, str):
        raise ValueError("analysis result is missing its input digest")
    completion_state = str(result.get("completion_state", ""))
    analytical_output = {
        "as_of_date": as_of.isoformat(),
        "timeframe": timeframe.value,
        "input_start_date": _normalize(result.get("input_start_date")),
        "input_end_date": _normalize(result.get("input_end_date")),
        "input_digest": input_digest,
        "algorithm_version": result.get("algorithm_version"),
        "config_version": result.get("config_version"),
        "completion_state": completion_state,
        "warnings": warnings,
        "items": canonical_items,
    }
    output_digest = hashlib.sha256(
        _canonical_json(analytical_output).encode("utf-8")
    ).hexdigest()
    return TrendReplaySnapshot(
        as_of,
        timeframe,
        input_digest,
        output_digest,
        completion_state,
        tuple(sorted(counts.items())),
        tuple(events),
        warnings_json,
        tuple(items),
    )


def _normalize(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _candidate_identity(
    item_type: str,
    payload: Mapping[str, object],
) -> tuple[object, ...] | None:
    if item_type == "line":
        return (
            "line", payload.get("horizon"), payload.get("kind"),
            payload.get("first_pivot_date"), payload.get("second_pivot_date"),
        )
    if item_type == "pattern":
        return (
            "pattern", payload.get("horizon"), payload.get("pattern_type"),
            payload.get("start_date"), payload.get("end_date"),
        )
    if item_type == "zone":
        evidence_dates = payload.get("evidence_dates")
        evidence_identity = (
            tuple(evidence_dates) if isinstance(evidence_dates, list) else ()
        )
        return (
            "zone", payload.get("kind"), evidence_identity,
            _rounded_number(payload.get("lower")),
            _rounded_number(payload.get("upper")),
        )
    return None


def _rounded_number(value: object) -> float | None:
    return round(float(value), 6) if isinstance(value, (int, float)) else None


def _jaccard(
    left: set[tuple[object, ...]],
    right: set[tuple[object, ...]],
) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _mean_or_none(values) -> float | None:
    collected = tuple(values)
    return sum(collected) / len(collected) if collected else None
