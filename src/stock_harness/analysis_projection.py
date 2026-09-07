"""Backend-owned core evidence projection for every analysis consumer."""

from __future__ import annotations

from collections.abc import Sequence

from stock_harness.analysis_results import GeneratedAnalysisItem, GeneratedItemType


CORE_PROJECTION_ITEM_ID = "core-analysis-projection"
CORE_PROJECTION_VERSION = "core-evidence-v1"


def build_core_projection_item(
    items: Sequence[GeneratedAnalysisItem],
) -> GeneratedAnalysisItem:
    line_ids = _core_line_ids(items)
    pattern_ids = _core_pattern_ids(items)
    zone_ids = _core_zone_ids(items)
    return GeneratedAnalysisItem(
        item_id=CORE_PROJECTION_ITEM_ID,
        item_type=GeneratedItemType.EVIDENCE,
        payload={
            "kind": "core-analysis-projection",
            "version": CORE_PROJECTION_VERSION,
            "line_item_ids": line_ids,
            "pattern_item_ids": pattern_ids,
            "zone_item_ids": zone_ids,
            "structural_item_ids": [*line_ids, *pattern_ids, *zone_ids],
        },
    )


def read_core_structural_item_ids(items: Sequence[dict[str, object]]) -> tuple[str, ...]:
    for item in items:
        payload = item.get("payload")
        if (
            item.get("item_id") != CORE_PROJECTION_ITEM_ID
            or not isinstance(payload, dict)
        ):
            continue
        values = payload.get("structural_item_ids")
        if not isinstance(values, list):
            return ()
        available = {
            str(candidate.get("item_id"))
            for candidate in items if isinstance(candidate, dict)
        }
        return tuple(
            str(value) for value in values
            if isinstance(value, str) and value in available
        )
    return ()


def _core_line_ids(items: Sequence[GeneratedAnalysisItem]) -> list[str]:
    selected: dict[tuple[str, str], GeneratedAnalysisItem] = {}
    for item in items:
        if item.item_type is not GeneratedItemType.LINE:
            continue
        kind = str(item.payload.get("kind", ""))
        horizon = str(item.payload.get("horizon", ""))
        if kind not in {"support", "resistance"} or horizon not in {
            "short", "medium", "long",
        }:
            continue
        key = (horizon, kind)
        current = selected.get(key)
        if current is None or _line_rank(item) > _line_rank(current):
            selected[key] = item
    return [selected[key].item_id for key in sorted(selected)]


def _line_rank(item: GeneratedAnalysisItem) -> tuple[float, int, str]:
    score = item.payload.get("score", 0)
    touches = item.payload.get("touch_count", 0)
    return (
        float(score) if isinstance(score, (int, float)) else 0.0,
        int(touches) if isinstance(touches, int) else 0,
        item.item_id,
    )


def _core_pattern_ids(items: Sequence[GeneratedAnalysisItem]) -> list[str]:
    by_horizon: dict[str, list[GeneratedAnalysisItem]] = {}
    for item in items:
        if item.item_type is GeneratedItemType.PATTERN:
            by_horizon.setdefault(str(item.payload.get("horizon", "unspecified")), []).append(item)
    selected = []
    for horizon in sorted(by_horizon):
        candidates = by_horizon[horizon]
        primary = next((item for item in candidates if item.payload.get("primary") is True), None)
        if primary is None:
            primary = min(candidates, key=_pattern_rank)
        selected.append(primary.item_id)
    return selected


def _pattern_rank(item: GeneratedAnalysisItem) -> tuple[float, float, str]:
    rank = item.payload.get("interpretation_rank")
    score = item.payload.get("ranking_score", item.payload.get("score", 0))
    return (
        float(rank) if isinstance(rank, (int, float)) else float("inf"),
        -(float(score) if isinstance(score, (int, float)) else 0.0),
        item.item_id,
    )


def _core_zone_ids(items: Sequence[GeneratedAnalysisItem]) -> list[str]:
    by_kind: dict[str, list[GeneratedAnalysisItem]] = {}
    for item in items:
        if item.item_type is not GeneratedItemType.ZONE:
            continue
        kind = str(item.payload.get("kind", ""))
        if kind in {"key-level", "estimated-volume-at-price"}:
            by_kind.setdefault(kind, []).append(item)
    selected = []
    for kind in sorted(by_kind):
        candidates = sorted(by_kind[kind], key=_zone_rank, reverse=True)
        selected.extend(item.item_id for item in candidates[:2])
    return selected


def _zone_rank(item: GeneratedAnalysisItem) -> tuple[float, str]:
    key = "estimated_share" if item.payload.get("kind") == "estimated-volume-at-price" else "score"
    value = item.payload.get(key, 0)
    return (float(value) if isinstance(value, (int, float)) else 0.0, item.item_id)
