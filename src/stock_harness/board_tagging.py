"""Deterministic projection from current board memberships to compact stock tags."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from collections.abc import Iterable


ALGORITHM_VERSION = "stock-board-tags-v1"
MAX_BOARD_TAGS = 3

_GENERIC_CONCEPTS = {
    "融资融券", "沪股通", "深股通", "标普概念", "富时罗素", "msci中国",
    "机构重仓", "基金重仓", "证金持股", "昨日涨停", "昨日连板", "预盈预增",
}
_NON_BUSINESS_MARKERS = (
    "预增", "预盈", "季报", "年报", "融资融券", "沪股通", "深股通",
    "机构重仓", "基金重仓", "证金持股", "昨日涨停", "昨日连板",
    "增持", "减持", "破净", "富时罗素", "msci", "标普概念",
)


@dataclass(frozen=True)
class BoardTagCandidate:
    board_id: int
    symbol: str
    name: str
    classification: str
    source_system: str
    exchange: str
    member_count: int


@dataclass(frozen=True)
class SelectedBoardTag:
    candidate: BoardTagCandidate
    position: int
    score: float
    reason: str


def select_board_tags(candidates: Iterable[BoardTagCandidate]) -> list[SelectedBoardTag]:
    grouped: dict[tuple[str, str], list[BoardTagCandidate]] = {}
    for item in candidates:
        if item.classification not in {"industry", "concept"}:
            continue
        grouped.setdefault((item.classification, _normalized_name(item.name)), []).append(item)

    industries = [_representative(rows) for (kind, _), rows in grouped.items() if kind == "industry"]
    concepts = [_representative(rows) for (kind, _), rows in grouped.items() if kind == "concept"]
    selected: list[tuple[BoardTagCandidate, float, str]] = []
    if industries:
        item = min(industries, key=_industry_sort_key)
        selected.append((item, _industry_score(item), f"structured-industry:{_source_tier(item)}"))

    concept_groups = {
        key: rows for key, rows in grouped.items() if key[0] == "concept"
    }
    business_concepts = [item for item in concepts if not _is_non_business_concept(item.name)]
    ranked_concepts = sorted(
        business_concepts or concepts,
        key=lambda item: _concept_sort_key(
            item, len(concept_groups[("concept", _normalized_name(item.name))])
        ),
    )
    for item in ranked_concepts[:2]:
        support = len(concept_groups[("concept", _normalized_name(item.name))])
        selected.append((
            item,
            _concept_score(item, support),
            f"concept:sources={support};members={item.member_count};tier={_source_tier(item)}",
        ))

    return [
        SelectedBoardTag(item, position, score, reason)
        for position, (item, score, reason) in enumerate(selected[:MAX_BOARD_TAGS])
    ]


def _representative(items: list[BoardTagCandidate]) -> BoardTagCandidate:
    if items[0].classification == "concept":
        return min(items, key=lambda item: (len(item.name), _source_tier(item), item.symbol))
    return min(items, key=lambda item: (_source_tier(item), item.member_count, item.symbol))


def _industry_sort_key(item: BoardTagCandidate) -> tuple[int, int, int, str]:
    size_band, size_distance = _specificity(item.member_count)
    return _source_tier(item), size_band, size_distance, item.symbol


def _concept_sort_key(item: BoardTagCandidate, support: int) -> tuple[int, int, int, int, int, str]:
    generic = int(_is_non_business_concept(item.name))
    size_band, size_distance = _specificity(item.member_count)
    return generic, -support, size_band, size_distance, _source_tier(item), item.symbol


def _source_tier(item: BoardTagCandidate) -> int:
    source = item.source_system.casefold()
    if item.classification == "industry" and (item.exchange == "SI" or source in {"sw", "shenwan"}):
        return 0
    if source == "ths":
        return 1
    if source == "eastmoney":
        return 2
    return 3


def _specificity(member_count: int) -> tuple[int, int]:
    count = max(1, member_count)
    if 10 <= count <= 300:
        return 0, abs(count - 80)
    if 5 <= count <= 600:
        return 1, abs(count - 80)
    return 2, abs(count - 80)


def _industry_score(item: BoardTagCandidate) -> float:
    size_band, distance = _specificity(item.member_count)
    return round(100 - _source_tier(item) * 10 - size_band * 5 - math.log1p(distance), 6)


def _concept_score(item: BoardTagCandidate, support: int) -> float:
    size_band, distance = _specificity(item.member_count)
    generic_penalty = 30 if _is_non_business_concept(item.name) else 0
    return round(100 + min(support, 3) * 10 - _source_tier(item) * 2 - size_band * 8 - math.log1p(distance) - generic_penalty, 6)


def _normalized_name(value: str) -> str:
    compact = re.sub(r"[\s·_-]+", "", value).casefold()
    for suffix in (
        "概念板块", "行业板块", "conceptboard", "industryboard",
        "概念", "行业", "板块", "concept", "industry", "board",
    ):
        if compact.endswith(suffix) and len(compact) > len(suffix):
            compact = compact[: -len(suffix)]
            break
    return compact


def _is_non_business_concept(value: str) -> bool:
    normalized = _normalized_name(value)
    return normalized in _GENERIC_CONCEPTS or any(
        marker in normalized for marker in _NON_BUSINESS_MARKERS
    )
