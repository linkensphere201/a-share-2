"""Typed structural evidence shared by every trade-scenario consumer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from stock_harness.analysis_results import GeneratedAnalysisItem, GeneratedItemType


@dataclass(frozen=True, slots=True)
class StructuralBoundary:
    source_item_id: str
    source_kind: str
    role: str
    horizon: str
    lower: float
    upper: float
    score: float
    slope_per_bar: float = 0

    @property
    def center(self) -> float:
        return (self.lower + self.upper) / 2


@dataclass(frozen=True, slots=True)
class StructuralSetup:
    source_item_id: str
    family: str
    direction: str
    horizon: str
    state: str
    boundary_price: float
    invalidation_price: float | None
    score: float
    start_date: str | None


@dataclass(frozen=True, slots=True)
class StructuralMap:
    boundaries: tuple[StructuralBoundary, ...]
    setups: tuple[StructuralSetup, ...]


def build_structural_map(
    items: Sequence[GeneratedAnalysisItem], latest_close: float
) -> StructuralMap:
    by_id = {item.item_id: item for item in items}
    core_ids = _core_ids(items)
    selected = core_ids or set(by_id)
    boundaries: list[StructuralBoundary] = []
    setups: list[StructuralSetup] = []

    for item in items:
        payload = item.payload
        if item.item_type is GeneratedItemType.ZONE:
            lower = _number(payload.get("lower"))
            upper = _number(payload.get("upper"))
            if lower is None or upper is None or lower <= 0 or upper < lower:
                continue
            role = "resistance" if lower > latest_close else "support" if upper < latest_close else "neutral"
            boundaries.append(StructuralBoundary(
                item.item_id, str(payload.get("kind", "zone")), role,
                str(payload.get("horizon", "long")), lower, upper,
                _number(payload.get("score")) or _number(payload.get("estimated_share")) or 0,
            ))
        elif item.item_type is GeneratedItemType.LINE and item.item_id in selected:
            price = _number(payload.get("projected_price"))
            role = str(payload.get("kind", ""))
            if price is None or price <= 0 or role not in {"support", "resistance"}:
                continue
            boundaries.append(StructuralBoundary(
                item.item_id, "trend-line", role,
                str(payload.get("horizon", "long")), price, price,
                _number(payload.get("score")) or 0,
                _number(payload.get("slope_per_bar")) or 0,
            ))
        elif (
            item.item_type is GeneratedItemType.EVIDENCE
            and payload.get("kind") == "structural-range-reference"
        ):
            horizon = str(payload.get("horizon_bars", ""))
            low = _number(payload.get("low"))
            high = _number(payload.get("high"))
            if low is not None and low > 0:
                boundaries.append(StructuralBoundary(
                    item.item_id, "historical-range-low", "support", horizon,
                    low, low, 0.2,
                ))
            if high is not None and high > 0:
                boundaries.append(StructuralBoundary(
                    item.item_id, "historical-range-high", "resistance", horizon,
                    high, high, 0.2,
                ))

    event_by_parent = _events_by_parent(items)
    for item in items:
        if item.item_type is not GeneratedItemType.PATTERN or item.item_id not in selected:
            continue
        payload = item.payload
        event = event_by_parent.get(item.item_id)
        direction = _direction(payload, event)
        boundary = _number((event or {}).get("boundary_price")) or _number(payload.get("neckline_price"))
        if direction is None or boundary is None or boundary <= 0:
            continue
        setups.append(StructuralSetup(
            item.item_id,
            str(payload.get("pattern_type", "pattern")),
            direction,
            str(payload.get("horizon", "medium")),
            _state(payload, event),
            boundary,
            _number(payload.get("invalidation_price"))
            or _number((event or {}).get("invalidation_level")),
            _number(payload.get("ranking_score")) or _number(payload.get("score")) or 0,
            str(payload.get("start_date")) if isinstance(payload.get("start_date"), str) else None,
        ))

    if not setups:
        for item in items:
            if item.item_type is not GeneratedItemType.LINE or item.item_id not in selected:
                continue
            payload = item.payload
            boundary = _number(payload.get("projected_price"))
            role = str(payload.get("kind", ""))
            if boundary is None or role not in {"support", "resistance"}:
                continue
            event = event_by_parent.get(item.item_id)
            setups.append(StructuralSetup(
                item.item_id,
                "trend-line-break",
                "long" if role == "resistance" else "short",
                str(payload.get("horizon", "long")),
                _state(payload, event),
                boundary,
                _number((event or {}).get("invalidation_level")),
                _number(payload.get("score")) or 0,
                str(payload.get("first_pivot_date"))
                if isinstance(payload.get("first_pivot_date"), str) else None,
            ))

    horizon_rank = {"short": 0, "medium": 1, "long": 2}
    setups.sort(key=lambda value: (
        value.state in {"invalidated", "failed"},
        -value.score,
        -horizon_rank.get(value.horizon, 0),
        value.source_item_id,
    ))
    return StructuralMap(tuple(boundaries), tuple(setups))


def _core_ids(items: Sequence[GeneratedAnalysisItem]) -> set[str]:
    projection = next((item for item in items if (
        item.item_type is GeneratedItemType.EVIDENCE
        and item.payload.get("kind") == "core-analysis-projection"
    )), None)
    if projection is None:
        return set()
    values = projection.payload.get("structural_item_ids")
    return {str(value) for value in values} if isinstance(values, list) else set()


def _events_by_parent(
    items: Sequence[GeneratedAnalysisItem],
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for item in items:
        if item.parent_item_id is None or item.item_type is not GeneratedItemType.EVIDENCE:
            continue
        if item.payload.get("kind") not in {
            "breakout-state-summary", "latest-structural-event-summary",
        }:
            continue
        result[item.parent_item_id] = dict(item.payload)
    return result


def _direction(
    payload: object, event: dict[str, object] | None
) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("direction")
    if value in {"bullish", "up", "long"}:
        return "long"
    if value in {"bearish", "down", "short"}:
        return "short"
    event_direction = (event or {}).get("direction")
    return "long" if event_direction == "up" else "short" if event_direction == "down" else None


def _state(payload: object, event: dict[str, object] | None) -> str:
    if isinstance(event, dict) and isinstance(event.get("current_state"), str):
        return str(event["current_state"])
    if isinstance(payload, dict) and isinstance(payload.get("completion_state"), str):
        return str(payload["completion_state"])
    return "forming"


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None
