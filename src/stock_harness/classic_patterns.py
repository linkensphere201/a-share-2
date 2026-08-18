"""Causal classic-pattern contracts and bounded daily detectors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.trend_pivots import PivotKind, PricePivot


class PatternType(StrEnum):
    DOUBLE_BOTTOM = "double-bottom"
    DOUBLE_TOP = "double-top"


class PatternDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class PatternState(StrEnum):
    FORMING = "forming"
    CONFIRMED = "confirmed"
    INVALIDATED = "invalidated"


@dataclass(frozen=True, slots=True)
class PatternConfig:
    endpoint_tolerance_percent: float = 0.04
    minimum_prominence_percent: float = 0.06
    minimum_leg_bars: int = 3
    maximum_leg_ratio: float = 3.0
    breakout_buffer_percent: float = 0.005
    invalidation_buffer_percent: float = 0.01
    max_candidates_per_type: int = 4

    def validate(self) -> None:
        if not 0.005 <= self.endpoint_tolerance_percent <= 0.15:
            raise ValueError("pattern endpoint tolerance must be between 0.5% and 15%")
        if not 0.01 <= self.minimum_prominence_percent <= 0.5:
            raise ValueError("pattern prominence must be between 1% and 50%")
        if self.minimum_leg_bars < 2:
            raise ValueError("pattern legs must span at least two bars")
        if not 1 <= self.maximum_leg_ratio <= 10:
            raise ValueError("maximum leg ratio must be between 1 and 10")


@dataclass(frozen=True, slots=True)
class PatternPivot:
    kind: str
    pivot_date: date
    price: float
    confirmed_date: date


@dataclass(frozen=True, slots=True)
class ClassicPattern:
    pattern_type: PatternType
    display_name: str
    direction: PatternDirection
    state: PatternState
    start_date: date
    end_date: date
    available_date: date
    pivots: tuple[PatternPivot, PatternPivot, PatternPivot]
    neckline_price: float
    breakout_date: date | None
    invalidation_price: float
    invalidation_date: date | None
    score: float
    score_components: dict[str, float]
    volume_ratio: float | None
    primary: bool = False


def detect_double_patterns(
    bars: Sequence[AnalysisBar],
    pivots: Sequence[PricePivot],
    config: PatternConfig = PatternConfig(),
) -> tuple[ClassicPattern, ...]:
    config.validate()
    if not bars:
        return ()
    ordered = sorted(bars, key=lambda item: item.period_end)
    index_by_date = {bar.period_end: index for index, bar in enumerate(ordered)}
    confirmed = [
        pivot for pivot in pivots
        if not pivot.tentative and pivot.confirmed_date is not None
        and pivot.pivot_date in index_by_date
    ]
    candidates: list[ClassicPattern] = []
    for first, middle, last in zip(confirmed, confirmed[1:], confirmed[2:]):
        kinds = (first.kind, middle.kind, last.kind)
        if kinds == (PivotKind.LOW, PivotKind.HIGH, PivotKind.LOW):
            candidate = _build_pattern(
                ordered, index_by_date, first, middle, last,
                PatternType.DOUBLE_BOTTOM, config,
            )
        elif kinds == (PivotKind.HIGH, PivotKind.LOW, PivotKind.HIGH):
            candidate = _build_pattern(
                ordered, index_by_date, first, middle, last,
                PatternType.DOUBLE_TOP, config,
            )
        else:
            candidate = None
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(key=lambda item: item.score, reverse=True)
    selected: list[ClassicPattern] = []
    counts = {PatternType.DOUBLE_BOTTOM: 0, PatternType.DOUBLE_TOP: 0}
    for candidate in candidates:
        if counts[candidate.pattern_type] >= config.max_candidates_per_type:
            continue
        selected.append(candidate)
        counts[candidate.pattern_type] += 1
    if not selected:
        return ()
    primary_index = max(range(len(selected)), key=lambda index: selected[index].score)
    return tuple(
        _with_primary(item, index == primary_index)
        for index, item in enumerate(selected)
    )


def _build_pattern(
    bars: Sequence[AnalysisBar],
    index_by_date: dict[date, int],
    first: PricePivot,
    middle: PricePivot,
    last: PricePivot,
    pattern_type: PatternType,
    config: PatternConfig,
) -> ClassicPattern | None:
    first_index = index_by_date[first.pivot_date]
    middle_index = index_by_date[middle.pivot_date]
    last_index = index_by_date[last.pivot_date]
    first_leg = middle_index - first_index
    second_leg = last_index - middle_index
    if min(first_leg, second_leg) < config.minimum_leg_bars:
        return None
    leg_ratio = max(first_leg, second_leg) / min(first_leg, second_leg)
    if leg_ratio > config.maximum_leg_ratio:
        return None
    endpoint_scale = max(abs(first.price), abs(last.price), 1e-9)
    endpoint_error = abs(first.price - last.price) / endpoint_scale
    if endpoint_error > config.endpoint_tolerance_percent:
        return None
    endpoint_reference = max(first.price, last.price) if pattern_type is PatternType.DOUBLE_BOTTOM else min(first.price, last.price)
    prominence = (
        (middle.price - endpoint_reference) / max(abs(endpoint_reference), 1e-9)
        if pattern_type is PatternType.DOUBLE_BOTTOM
        else (endpoint_reference - middle.price) / max(abs(endpoint_reference), 1e-9)
    )
    if prominence < config.minimum_prominence_percent:
        return None

    available_date = max(first.confirmed_date, middle.confirmed_date, last.confirmed_date)
    neckline = middle.price
    invalidation_price = (
        min(first.price, last.price) * (1 - config.invalidation_buffer_percent)
        if pattern_type is PatternType.DOUBLE_BOTTOM
        else max(first.price, last.price) * (1 + config.invalidation_buffer_percent)
    )
    breakout_threshold = (
        neckline * (1 + config.breakout_buffer_percent)
        if pattern_type is PatternType.DOUBLE_BOTTOM
        else neckline * (1 - config.breakout_buffer_percent)
    )
    breakout_date: date | None = None
    invalidation_date: date | None = None
    trigger_index: int | None = None
    for index, bar in enumerate(bars):
        if bar.period_end < available_date:
            continue
        invalid = bar.close < invalidation_price if pattern_type is PatternType.DOUBLE_BOTTOM else bar.close > invalidation_price
        breakout = bar.close > breakout_threshold if pattern_type is PatternType.DOUBLE_BOTTOM else bar.close < breakout_threshold
        if breakout_date is None and breakout:
            breakout_date = bar.period_end
            trigger_index = index
        if invalid:
            invalidation_date = bar.period_end
            break
    state = (
        PatternState.INVALIDATED if invalidation_date is not None
        else PatternState.CONFIRMED if breakout_date is not None
        else PatternState.FORMING
    )
    similarity_score = 1 - endpoint_error / config.endpoint_tolerance_percent
    prominence_score = min(1.0, prominence / (config.minimum_prominence_percent * 2))
    symmetry_score = 1 / leg_ratio
    duration_score = min(1.0, (last_index - first_index) / 40)
    volume_ratio = _volume_ratio(bars, trigger_index) if trigger_index is not None else None
    volume_score = min(1.0, (volume_ratio or 0) / 1.5)
    score = (
        0.30 * similarity_score + 0.25 * prominence_score
        + 0.20 * symmetry_score + 0.10 * duration_score + 0.15 * volume_score
    )
    return ClassicPattern(
        pattern_type=pattern_type,
        display_name="双底" if pattern_type is PatternType.DOUBLE_BOTTOM else "双顶",
        direction=PatternDirection.BULLISH if pattern_type is PatternType.DOUBLE_BOTTOM else PatternDirection.BEARISH,
        state=state,
        start_date=first.pivot_date,
        end_date=last.pivot_date,
        available_date=available_date,
        pivots=tuple(
            PatternPivot(item.kind.value, item.pivot_date, item.price, item.confirmed_date)
            for item in (first, middle, last)
        ),
        neckline_price=neckline,
        breakout_date=breakout_date,
        invalidation_price=invalidation_price,
        invalidation_date=invalidation_date,
        score=round(score, 6),
        score_components={
            "endpoint_similarity": round(similarity_score, 6),
            "prominence": round(prominence_score, 6),
            "symmetry": round(symmetry_score, 6),
            "duration": round(duration_score, 6),
            "breakout_volume": round(volume_score, 6),
        },
        volume_ratio=round(volume_ratio, 6) if volume_ratio is not None else None,
    )


def _volume_ratio(bars: Sequence[AnalysisBar], index: int) -> float | None:
    prior = [bar.volume for bar in bars[max(0, index - 20):index] if bar.volume > 0]
    if not prior or bars[index].volume <= 0:
        return None
    return bars[index].volume / (sum(prior) / len(prior))


def _with_primary(value: ClassicPattern, primary: bool) -> ClassicPattern:
    return ClassicPattern(
        value.pattern_type, value.display_name, value.direction, value.state,
        value.start_date, value.end_date, value.available_date, value.pivots,
        value.neckline_price, value.breakout_date, value.invalidation_price,
        value.invalidation_date, value.score, value.score_components,
        value.volume_ratio, primary,
    )
