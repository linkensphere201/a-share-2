"""Causal rectangle and triangle detectors from confirmed alternating pivots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from math import sqrt
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.breakout_state import (
    evaluate_pattern_boundaries,
    resolve_pattern_completion_state,
)
from stock_harness.trend_pivots import PivotKind, PricePivot


class ConsolidationType(StrEnum):
    RECTANGLE = "rectangle"
    ASCENDING_TRIANGLE = "ascending-triangle"
    DESCENDING_TRIANGLE = "descending-triangle"
    SYMMETRICAL_TRIANGLE = "symmetrical-triangle"
    BULL_FLAG = "bull-flag"
    BEAR_FLAG = "bear-flag"


@dataclass(frozen=True, slots=True)
class ConsolidationConfig:
    minimum_pivots: int = 4
    maximum_pivots: int = 8
    minimum_duration_bars: int = 8
    flat_change_percent: float = 0.03
    directional_change_percent: float = 0.04
    maximum_boundary_residual_percent: float = 0.035
    maximum_triangle_end_width_ratio: float = 0.8
    breakout_buffer_percent: float = 0.005
    minimum_flag_impulse_percent: float = 0.08
    maximum_flag_parallel_change_gap: float = 0.03
    max_candidates: int = 6


@dataclass(frozen=True, slots=True)
class BoundaryLine:
    start_date: date
    start_price: float
    end_date: date
    end_price: float
    slope_per_bar: float


@dataclass(frozen=True, slots=True)
class ConsolidationPattern:
    pattern_type: ConsolidationType
    display_name: str
    start_date: date
    end_date: date
    available_date: date
    pivots: tuple[PricePivot, ...]
    upper_boundary: BoundaryLine
    lower_boundary: BoundaryLine
    completion_state: str
    breakout_direction: str | None
    breakout_date: date | None
    invalidation_date: date | None
    score: float
    score_components: dict[str, float]
    volume_ratio: float | None
    primary: bool = False


def detect_consolidation_patterns(
    bars: Sequence[AnalysisBar],
    pivots: Sequence[PricePivot],
    config: ConsolidationConfig = ConsolidationConfig(),
) -> tuple[ConsolidationPattern, ...]:
    ordered = sorted(bars, key=lambda item: item.period_end)
    index_by_date = {bar.period_end: index for index, bar in enumerate(ordered)}
    confirmed = [
        pivot for pivot in pivots
        if not pivot.tentative and pivot.confirmed_date is not None
        and pivot.pivot_date in index_by_date
    ]
    candidates: list[ConsolidationPattern] = []
    for end in range(config.minimum_pivots, len(confirmed) + 1):
        for size in range(config.minimum_pivots, min(config.maximum_pivots, end) + 1):
            candidate = _fit_window(
                ordered, index_by_date, confirmed[end - size:end], config
            )
            if candidate is not None:
                candidates.append(candidate)
    best_by_identity: dict[tuple[ConsolidationType, date, date], ConsolidationPattern] = {}
    for candidate in candidates:
        key = (candidate.pattern_type, candidate.start_date, candidate.end_date)
        if key not in best_by_identity or candidate.score > best_by_identity[key].score:
            best_by_identity[key] = candidate
    selected = sorted(best_by_identity.values(), key=lambda item: item.score, reverse=True)[
        :config.max_candidates
    ]
    return tuple(_with_primary(item, index == 0) for index, item in enumerate(selected))


def _fit_window(
    bars: Sequence[AnalysisBar],
    index_by_date: dict[date, int],
    pivots: Sequence[PricePivot],
    config: ConsolidationConfig,
) -> ConsolidationPattern | None:
    highs = [item for item in pivots if item.kind is PivotKind.HIGH]
    lows = [item for item in pivots if item.kind is PivotKind.LOW]
    if len(highs) < 2 or len(lows) < 2:
        return None
    start_index = index_by_date[pivots[0].pivot_date]
    end_index = index_by_date[pivots[-1].pivot_date]
    duration = end_index - start_index
    if duration < config.minimum_duration_bars:
        return None
    upper_slope, upper_intercept, upper_residual = _fit_boundary(highs, index_by_date)
    lower_slope, lower_intercept, lower_residual = _fit_boundary(lows, index_by_date)
    scale = max(
        1e-9,
        sum(item.price for item in pivots) / len(pivots),
    )
    if max(upper_residual, lower_residual) / scale > config.maximum_boundary_residual_percent:
        return None
    upper_start = upper_intercept + upper_slope * start_index
    upper_end = upper_intercept + upper_slope * end_index
    lower_start = lower_intercept + lower_slope * start_index
    lower_end = lower_intercept + lower_slope * end_index
    start_width = upper_start - lower_start
    end_width = upper_end - lower_end
    if start_width <= 0 or end_width <= 0:
        return None
    upper_change = (upper_end - upper_start) / scale
    lower_change = (lower_end - lower_start) / scale
    upper_flat = abs(upper_change) <= config.flat_change_percent
    lower_flat = abs(lower_change) <= config.flat_change_percent
    contracting = end_width / start_width <= config.maximum_triangle_end_width_ratio
    prior_index = max(0, start_index - duration)
    prior_close = bars[prior_index].close
    impulse = bars[start_index].close / prior_close - 1 if prior_close else 0.0
    parallel = abs(upper_change - lower_change) <= config.maximum_flag_parallel_change_gap
    if upper_flat and lower_flat and abs(end_width / start_width - 1) <= 0.3:
        pattern_type = ConsolidationType.RECTANGLE
    elif upper_flat and lower_change >= config.directional_change_percent and contracting:
        pattern_type = ConsolidationType.ASCENDING_TRIANGLE
    elif lower_flat and upper_change <= -config.directional_change_percent and contracting:
        pattern_type = ConsolidationType.DESCENDING_TRIANGLE
    elif (
        upper_change <= -config.directional_change_percent
        and lower_change >= config.directional_change_percent
        and contracting
    ):
        pattern_type = ConsolidationType.SYMMETRICAL_TRIANGLE
    elif (
        impulse >= config.minimum_flag_impulse_percent
        and parallel
        and max(upper_change, lower_change) <= config.flat_change_percent
    ):
        pattern_type = ConsolidationType.BULL_FLAG
    elif (
        impulse <= -config.minimum_flag_impulse_percent
        and parallel
        and min(upper_change, lower_change) >= -config.flat_change_percent
    ):
        pattern_type = ConsolidationType.BEAR_FLAG
    else:
        return None
    available_date = max(item.confirmed_date for item in pivots)
    events = evaluate_pattern_boundaries(
        bars,
        available_date=available_date,
        upper_price_at=lambda index: upper_intercept + upper_slope * index,
        lower_price_at=lambda index: lower_intercept + lower_slope * index,
        buffer_percent=config.breakout_buffer_percent,
    )
    breakout_direction = events.direction.value if events.direction is not None else None
    residual_score = 1 - min(1.0, max(upper_residual, lower_residual) / scale / config.maximum_boundary_residual_percent)
    contraction_score = min(1.0, max(0.0, 1 - end_width / start_width) / 0.6)
    impulse_score = min(1.0, abs(impulse) / (config.minimum_flag_impulse_percent * 2))
    duration_score = min(1.0, duration / 60)
    pivot_score = min(1.0, len(pivots) / config.maximum_pivots)
    shape_score = (
        impulse_score
        if pattern_type in {ConsolidationType.BULL_FLAG, ConsolidationType.BEAR_FLAG}
        else contraction_score
    )
    score = 0.35 * residual_score + 0.30 * shape_score + 0.20 * duration_score + 0.15 * pivot_score
    volume_ratio = (
        _volume_ratio(bars, events.trigger_index)
        if events.trigger_index is not None else None
    )
    return ConsolidationPattern(
        pattern_type=pattern_type,
        display_name={
            ConsolidationType.RECTANGLE: "震荡平台",
            ConsolidationType.ASCENDING_TRIANGLE: "上升三角形",
            ConsolidationType.DESCENDING_TRIANGLE: "下降三角形",
            ConsolidationType.SYMMETRICAL_TRIANGLE: "对称三角形",
            ConsolidationType.BULL_FLAG: "多头旗形",
            ConsolidationType.BEAR_FLAG: "空头旗形",
        }[pattern_type],
        start_date=pivots[0].pivot_date,
        end_date=pivots[-1].pivot_date,
        available_date=available_date,
        pivots=tuple(pivots),
        upper_boundary=BoundaryLine(
            pivots[0].pivot_date, upper_start, pivots[-1].pivot_date, upper_end, upper_slope
        ),
        lower_boundary=BoundaryLine(
            pivots[0].pivot_date, lower_start, pivots[-1].pivot_date, lower_end, lower_slope
        ),
        completion_state=resolve_pattern_completion_state(
            events.breakout_date, events.invalidation_date
        ),
        breakout_direction=breakout_direction,
        breakout_date=events.breakout_date,
        invalidation_date=events.invalidation_date,
        score=round(score, 6),
        score_components={
            "boundary_fit": round(residual_score, 6),
            "contraction": round(contraction_score, 6),
            "prior_impulse": round(impulse_score, 6),
            "duration": round(duration_score, 6),
            "pivot_count": round(pivot_score, 6),
        },
        volume_ratio=round(volume_ratio, 6) if volume_ratio is not None else None,
    )


def _fit_boundary(
    pivots: Sequence[PricePivot], index_by_date: dict[date, int]
) -> tuple[float, float, float]:
    xs = [index_by_date[item.pivot_date] for item in pivots]
    ys = [item.price for item in pivots]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denominator = sum((value - mean_x) ** 2 for value in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
    intercept = mean_y - slope * mean_x
    residual = sqrt(sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys)) / len(xs))
    return slope, intercept, residual


def _volume_ratio(bars: Sequence[AnalysisBar], index: int) -> float | None:
    prior = [item.volume for item in bars[max(0, index - 20):index] if item.volume > 0]
    if not prior or bars[index].volume <= 0:
        return None
    return bars[index].volume / (sum(prior) / len(prior))


def _with_primary(value: ConsolidationPattern, primary: bool) -> ConsolidationPattern:
    return ConsolidationPattern(
        value.pattern_type, value.display_name, value.start_date, value.end_date,
        value.available_date, value.pivots, value.upper_boundary,
        value.lower_boundary, value.completion_state, value.breakout_direction,
        value.breakout_date, value.invalidation_date, value.score,
        value.score_components, value.volume_ratio, primary,
    )
