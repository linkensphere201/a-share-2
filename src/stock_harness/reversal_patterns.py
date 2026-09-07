"""Causal V and head-and-shoulders reversal pattern detection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.breakout_state import (
    BreakoutDirection,
    evaluate_directional_pattern_boundary,
    resolve_pattern_completion_state,
)
from stock_harness.consolidation_patterns import BoundaryLine
from stock_harness.trend_pivots import PivotKind, PricePivot


class ReversalType(StrEnum):
    V_BOTTOM = "v-bottom"
    V_TOP = "v-top"
    HEAD_SHOULDERS_TOP = "head-shoulders-top"
    HEAD_SHOULDERS_BOTTOM = "head-shoulders-bottom"


@dataclass(frozen=True, slots=True)
class ReversalConfig:
    minimum_v_move_percent: float = 0.08
    minimum_v_recovery_ratio: float = 0.75
    maximum_v_leg_bars: int = 20
    maximum_leg_ratio: float = 3.0
    shoulder_tolerance_percent: float = 0.06
    minimum_head_prominence_percent: float = 0.06
    minimum_leg_bars: int = 2
    breakout_buffer_percent: float = 0.005
    invalidation_buffer_percent: float = 0.01
    max_candidates_per_type: int = 3


@dataclass(frozen=True, slots=True)
class ReversalPattern:
    pattern_type: ReversalType
    display_name: str
    direction: str
    state: str
    start_date: date
    end_date: date
    available_date: date
    pivots: tuple[PricePivot, ...]
    boundary_segments: tuple[BoundaryLine, ...]
    neckline_price: float
    neckline_slope_per_bar: float
    breakout_date: date | None
    invalidation_price: float
    invalidation_date: date | None
    score: float
    score_components: dict[str, float]
    volume_ratio: float | None
    primary: bool = False


def detect_reversal_patterns(
    bars: Sequence[AnalysisBar],
    pivots: Sequence[PricePivot],
    config: ReversalConfig = ReversalConfig(),
) -> tuple[ReversalPattern, ...]:
    ordered = sorted(bars, key=lambda item: item.period_end)
    index_by_date = {bar.period_end: index for index, bar in enumerate(ordered)}
    confirmed = [
        item for item in pivots
        if not item.tentative and item.confirmed_date is not None
        and item.pivot_date in index_by_date
    ]
    candidates: list[ReversalPattern] = []
    for pivot in confirmed:
        candidate = _fit_v(ordered, index_by_date, pivot, config)
        if candidate is not None:
            candidates.append(candidate)
    for window_start in range(max(0, len(confirmed) - 12), len(confirmed) - 4):
        candidate = _fit_head_shoulders(
            ordered, index_by_date, confirmed[window_start:window_start + 5], config
        )
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(key=lambda item: item.score, reverse=True)
    selected: list[ReversalPattern] = []
    counts = {item: 0 for item in ReversalType}
    seen: set[tuple[ReversalType, date, date]] = set()
    for candidate in candidates:
        identity = (candidate.pattern_type, candidate.start_date, candidate.end_date)
        if identity in seen or counts[candidate.pattern_type] >= config.max_candidates_per_type:
            continue
        seen.add(identity)
        selected.append(candidate)
        counts[candidate.pattern_type] += 1
    return tuple(_with_primary(item, index == 0) for index, item in enumerate(selected))


def _fit_v(
    bars: Sequence[AnalysisBar],
    index_by_date: dict[date, int],
    pivot: PricePivot,
    config: ReversalConfig,
) -> ReversalPattern | None:
    pivot_index = index_by_date[pivot.pivot_date]
    left_start = max(0, pivot_index - config.maximum_v_leg_bars)
    if pivot_index - left_start < config.minimum_leg_bars:
        return None
    right_end = min(len(bars) - 1, pivot_index + config.maximum_v_leg_bars)
    if right_end - pivot_index < config.minimum_leg_bars:
        return None
    if pivot.kind is PivotKind.LOW:
        left_index = max(range(left_start, pivot_index), key=lambda index: bars[index].high)
        left_price = bars[left_index].high
        decline = (left_price - pivot.price) / max(abs(left_price), 1e-9)
        recovery_target = pivot.price + (left_price - pivot.price) * config.minimum_v_recovery_ratio
        recovery_indexes = [
            index for index in range(pivot_index + 1, right_end + 1)
            if bars[index].close >= recovery_target
        ]
        pattern_type = ReversalType.V_BOTTOM
        direction = "bullish"
    else:
        left_index = min(range(left_start, pivot_index), key=lambda index: bars[index].low)
        left_price = bars[left_index].low
        decline = (pivot.price - left_price) / max(abs(left_price), 1e-9)
        recovery_target = pivot.price - (pivot.price - left_price) * config.minimum_v_recovery_ratio
        recovery_indexes = [
            index for index in range(pivot_index + 1, right_end + 1)
            if bars[index].close <= recovery_target
        ]
        pattern_type = ReversalType.V_TOP
        direction = "bearish"
    if decline < config.minimum_v_move_percent or not recovery_indexes:
        return None
    recovery_index = recovery_indexes[0]
    left_leg = pivot_index - left_index
    right_leg = recovery_index - pivot_index
    leg_ratio = max(left_leg, right_leg) / min(left_leg, right_leg)
    if leg_ratio > config.maximum_leg_ratio:
        return None
    available_date = max(pivot.confirmed_date, bars[recovery_index].period_end)
    invalidation_price = (
        pivot.price * (1 - config.invalidation_buffer_percent)
        if pattern_type is ReversalType.V_BOTTOM
        else pivot.price * (1 + config.invalidation_buffer_percent)
    )
    events = evaluate_directional_pattern_boundary(
        bars,
        available_date=bars[recovery_index].period_end,
        direction=(
            BreakoutDirection.UP
            if pattern_type is ReversalType.V_BOTTOM
            else BreakoutDirection.DOWN
        ),
        boundary_price_at=lambda _index: recovery_target,
        invalidation_price=invalidation_price,
        buffer_percent=0,
        invalidation_requires_trigger=True,
        inclusive_trigger=True,
    )
    move_score = min(1.0, decline / (config.minimum_v_move_percent * 2))
    symmetry_score = 1 / leg_ratio
    recovery_score = min(1.0, (
        abs(bars[recovery_index].close - pivot.price)
        / max(abs(left_price - pivot.price), 1e-9)
    ))
    volume_ratio = _volume_ratio(bars, recovery_index)
    volume_score = min(1.0, (volume_ratio or 0) / 1.5)
    score = 0.35 * move_score + 0.30 * symmetry_score + 0.25 * recovery_score + 0.10 * volume_score
    pivot_copy = PricePivot(
        pivot.kind, pivot.pivot_date, pivot.price, pivot.confirmed_date,
        pivot.threshold, False,
    )
    return ReversalPattern(
        pattern_type=pattern_type,
        display_name="\u0056\u5f62\u5e95" if pattern_type is ReversalType.V_BOTTOM else "\u5012\u0056\u5f62\u9876",
        direction=direction,
        state=resolve_pattern_completion_state(
            events.breakout_date, events.invalidation_date
        ),
        start_date=bars[left_index].period_end,
        end_date=bars[recovery_index].period_end,
        available_date=available_date,
        pivots=(pivot_copy,),
        boundary_segments=(
            BoundaryLine(
                bars[left_index].period_end, left_price,
                pivot.pivot_date, pivot.price,
                (pivot.price - left_price) / left_leg,
            ),
            BoundaryLine(
                pivot.pivot_date, pivot.price,
                bars[recovery_index].period_end, bars[recovery_index].close,
                (bars[recovery_index].close - pivot.price) / right_leg,
            ),
        ),
        neckline_price=recovery_target,
        neckline_slope_per_bar=0.0,
        breakout_date=events.breakout_date,
        invalidation_price=invalidation_price,
        invalidation_date=events.invalidation_date,
        score=round(score, 6),
        score_components={
            "move": round(move_score, 6),
            "symmetry": round(symmetry_score, 6),
            "recovery": round(recovery_score, 6),
            "volume": round(volume_score, 6),
        },
        volume_ratio=round(volume_ratio, 6) if volume_ratio is not None else None,
    )


def _fit_head_shoulders(
    bars: Sequence[AnalysisBar],
    index_by_date: dict[date, int],
    pivots: Sequence[PricePivot],
    config: ReversalConfig,
) -> ReversalPattern | None:
    if len(pivots) != 5:
        return None
    kinds = tuple(item.kind for item in pivots)
    top = kinds == (
        PivotKind.HIGH, PivotKind.LOW, PivotKind.HIGH,
        PivotKind.LOW, PivotKind.HIGH,
    )
    bottom = kinds == (
        PivotKind.LOW, PivotKind.HIGH, PivotKind.LOW,
        PivotKind.HIGH, PivotKind.LOW,
    )
    if not top and not bottom:
        return None
    indexes = [index_by_date[item.pivot_date] for item in pivots]
    legs = [right - left for left, right in zip(indexes, indexes[1:])]
    if min(legs) < config.minimum_leg_bars:
        return None
    shoulder_scale = max(abs(pivots[0].price), abs(pivots[4].price), 1e-9)
    shoulder_error = abs(pivots[0].price - pivots[4].price) / shoulder_scale
    if shoulder_error > config.shoulder_tolerance_percent:
        return None
    shoulder_reference = max(pivots[0].price, pivots[4].price) if top else min(pivots[0].price, pivots[4].price)
    prominence = (
        (pivots[2].price - shoulder_reference) / max(abs(shoulder_reference), 1e-9)
        if top else (shoulder_reference - pivots[2].price) / max(abs(shoulder_reference), 1e-9)
    )
    if prominence < config.minimum_head_prominence_percent:
        return None
    neckline_first, neckline_second = pivots[1], pivots[3]
    neckline_span = indexes[3] - indexes[1]
    neckline_slope = (neckline_second.price - neckline_first.price) / neckline_span
    available_date = max(item.confirmed_date for item in pivots)
    latest_index = len(bars) - 1
    neckline_latest = neckline_first.price + neckline_slope * (latest_index - indexes[1])
    invalidation_price = (
        pivots[2].price * (1 + config.invalidation_buffer_percent)
        if top else pivots[2].price * (1 - config.invalidation_buffer_percent)
    )
    events = evaluate_directional_pattern_boundary(
        bars,
        available_date=available_date,
        direction=BreakoutDirection.DOWN if top else BreakoutDirection.UP,
        boundary_price_at=lambda index: (
            neckline_first.price + neckline_slope * (index - indexes[1])
        ),
        invalidation_price=invalidation_price,
        buffer_percent=config.breakout_buffer_percent,
        invalidation_requires_trigger=True,
    )
    left_duration = indexes[2] - indexes[0]
    right_duration = indexes[4] - indexes[2]
    duration_ratio = max(left_duration, right_duration) / min(left_duration, right_duration)
    if duration_ratio > config.maximum_leg_ratio:
        return None
    shoulder_score = 1 - shoulder_error / config.shoulder_tolerance_percent
    prominence_score = min(1.0, prominence / (config.minimum_head_prominence_percent * 2))
    symmetry_score = 1 / duration_ratio
    neckline_score = 1 - min(1.0, abs(neckline_second.price - neckline_first.price) / shoulder_scale / 0.12)
    volume_ratio = _volume_ratio(bars, events.trigger_index)
    volume_score = min(1.0, (volume_ratio or 0) / 1.5)
    score = 0.25 * shoulder_score + 0.30 * prominence_score + 0.20 * symmetry_score + 0.15 * neckline_score + 0.10 * volume_score
    return ReversalPattern(
        pattern_type=(ReversalType.HEAD_SHOULDERS_TOP if top else ReversalType.HEAD_SHOULDERS_BOTTOM),
        display_name="\u5934\u80a9\u9876" if top else "\u5934\u80a9\u5e95",
        direction="bearish" if top else "bullish",
        state=resolve_pattern_completion_state(
            events.breakout_date, events.invalidation_date
        ),
        start_date=pivots[0].pivot_date,
        end_date=pivots[4].pivot_date,
        available_date=available_date,
        pivots=tuple(pivots),
        boundary_segments=(BoundaryLine(
            neckline_first.pivot_date, neckline_first.price,
            neckline_second.pivot_date, neckline_second.price, neckline_slope,
        ),),
        neckline_price=neckline_latest,
        neckline_slope_per_bar=neckline_slope,
        breakout_date=events.breakout_date,
        invalidation_price=invalidation_price,
        invalidation_date=events.invalidation_date,
        score=round(score, 6),
        score_components={
            "shoulder_similarity": round(shoulder_score, 6),
            "head_prominence": round(prominence_score, 6),
            "symmetry": round(symmetry_score, 6),
            "neckline": round(neckline_score, 6),
            "volume": round(volume_score, 6),
        },
        volume_ratio=round(volume_ratio, 6) if volume_ratio is not None else None,
    )


def _volume_ratio(bars: Sequence[AnalysisBar], index: int | None) -> float | None:
    if index is None:
        return None
    prior = [item.volume for item in bars[max(0, index - 20):index] if item.volume > 0]
    if not prior or bars[index].volume <= 0:
        return None
    return bars[index].volume / (sum(prior) / len(prior))


def _with_primary(value: ReversalPattern, primary: bool) -> ReversalPattern:
    return ReversalPattern(
        value.pattern_type, value.display_name, value.direction, value.state,
        value.start_date, value.end_date, value.available_date, value.pivots,
        value.boundary_segments, value.neckline_price,
        value.neckline_slope_per_bar, value.breakout_date,
        value.invalidation_price, value.invalidation_date, value.score,
        value.score_components, value.volume_ratio, primary,
    )
