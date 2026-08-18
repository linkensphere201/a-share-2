"""Cross-family ranking for persisted pattern candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

from stock_harness.analysis_results import GeneratedAnalysisItem, GeneratedItemType


@dataclass(frozen=True, slots=True)
class _Candidate:
    position: int
    item: GeneratedAnalysisItem
    horizon: str
    start: date
    end: date
    state: str
    score: float


_STATE_QUALITY = {
    "confirmed": 1.0,
    "ready": 0.95,
    "triggered": 0.95,
    "forming": 0.8,
    "invalidated": 0.1,
    "failed": 0.05,
}


def rank_pattern_candidates(
    items: Sequence[GeneratedAnalysisItem],
    *,
    minimum_overlap_ratio: float = 0.35,
) -> list[GeneratedAnalysisItem]:
    """Rank candidates per horizon while preserving every original item."""
    if not 0 <= minimum_overlap_ratio <= 1:
        raise ValueError("minimum overlap ratio must be between zero and one")
    candidates = _read_candidates(items)
    replacements: dict[int, GeneratedAnalysisItem] = {}
    horizons = sorted({candidate.horizon for candidate in candidates})
    for horizon in horizons:
        scoped = [candidate for candidate in candidates if candidate.horizon == horizon]
        latest_end = max(candidate.end for candidate in scoped)
        earliest_end = min(candidate.end for candidate in scoped)
        recency_span = max(1, (latest_end - earliest_end).days)
        ranked = sorted(
            scoped,
            key=lambda candidate: (
                candidate.state not in {"invalidated", "failed"},
                _rank_score(candidate, earliest_end, recency_span),
                candidate.end,
                candidate.score,
                candidate.item.item_id,
            ),
            reverse=True,
        )
        primary_id = ranked[0].item.item_id
        groups = _overlap_groups(scoped, minimum_overlap_ratio)
        group_by_id = {
            item_id: (group_index, group)
            for group_index, group in enumerate(groups)
            for item_id in group
        }
        rank_by_id = {
            candidate.item.item_id: index + 1
            for index, candidate in enumerate(ranked)
        }
        for candidate in scoped:
            group_index, group = group_by_id[candidate.item.item_id]
            peers = sorted(group - {candidate.item.item_id})
            nested = sorted(
                peer.item.item_id for peer in scoped
                if peer.item.item_id in group
                and peer.item.item_id != candidate.item.item_id
                and _is_nested(candidate, peer)
            )
            payload = dict(candidate.item.payload)
            payload.update({
                "primary": candidate.item.item_id == primary_id,
                "interpretation_rank": rank_by_id[candidate.item.item_id],
                "ranking_score": round(
                    _rank_score(candidate, earliest_end, recency_span), 6
                ),
                "overlap_group": (
                    f"{horizon}-overlap-{group_index}" if len(group) > 1 else None
                ),
                "overlaps_with": peers,
                "nested_with": nested,
            })
            replacements[candidate.position] = GeneratedAnalysisItem(
                item_id=candidate.item.item_id,
                item_type=candidate.item.item_type,
                payload=payload,
                parent_item_id=candidate.item.parent_item_id,
            )
    return [replacements.get(index, item) for index, item in enumerate(items)]


def _read_candidates(items: Sequence[GeneratedAnalysisItem]) -> list[_Candidate]:
    result: list[_Candidate] = []
    for position, item in enumerate(items):
        if item.item_type is not GeneratedItemType.PATTERN:
            continue
        try:
            start = date.fromisoformat(str(item.payload["start_date"]))
            end = date.fromisoformat(str(item.payload["end_date"]))
        except (KeyError, TypeError, ValueError):
            continue
        if end < start:
            continue
        raw_score = item.payload.get("score", 0.0)
        score = float(raw_score) if isinstance(raw_score, (int, float)) else 0.0
        result.append(_Candidate(
            position=position,
            item=item,
            horizon=str(item.payload.get("horizon", "long")),
            start=start,
            end=end,
            state=str(item.payload.get("completion_state", "forming")),
            score=max(0.0, min(1.0, score)),
        ))
    return result


def _rank_score(candidate: _Candidate, earliest_end: date, span: int) -> float:
    recency = (candidate.end - earliest_end).days / span
    state = _STATE_QUALITY.get(candidate.state, 0.5)
    return 0.55 * candidate.score + 0.30 * state + 0.15 * recency


def _overlap_groups(
    candidates: Sequence[_Candidate], minimum_ratio: float
) -> list[set[str]]:
    remaining = {candidate.item.item_id for candidate in candidates}
    by_id = {candidate.item.item_id: candidate for candidate in candidates}
    groups: list[set[str]] = []
    while remaining:
        seed = min(remaining)
        group = {seed}
        frontier = [seed]
        remaining.remove(seed)
        while frontier:
            current = by_id[frontier.pop()]
            neighbors = [
                item_id for item_id in remaining
                if _overlap_ratio(current, by_id[item_id]) >= minimum_ratio
            ]
            for item_id in neighbors:
                remaining.remove(item_id)
                group.add(item_id)
                frontier.append(item_id)
        groups.append(group)
    groups.sort(key=lambda group: min(group))
    return groups


def _overlap_ratio(left: _Candidate, right: _Candidate) -> float:
    overlap = (min(left.end, right.end) - max(left.start, right.start)).days + 1
    if overlap <= 0:
        return 0.0
    shorter = min(
        (left.end - left.start).days + 1,
        (right.end - right.start).days + 1,
    )
    return overlap / shorter


def _is_nested(left: _Candidate, right: _Candidate) -> bool:
    return (
        left.start >= right.start and left.end <= right.end
    ) or (
        right.start >= left.start and right.end <= left.end
    )
