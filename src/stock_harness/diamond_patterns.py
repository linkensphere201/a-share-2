"""Causal broadening-then-contracting diamond pattern detection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from math import sqrt
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.consolidation_patterns import BoundaryLine
from stock_harness.trend_pivots import PivotKind, PricePivot


class DiamondType(StrEnum):
    DIAMOND = "diamond"
    DIAMOND_TOP = "diamond-top"
    DIAMOND_BOTTOM = "diamond-bottom"


@dataclass(frozen=True, slots=True)
class DiamondConfig:
    minimum_pivots: int = 7
    maximum_pivots: int = 10
    minimum_duration_bars: int = 8
    minimum_boundary_change_percent: float = 0.025
    minimum_expansion_ratio: float = 1.2
    maximum_end_width_ratio: float = 0.8
    maximum_boundary_residual_percent: float = 0.04
    context_trend_percent: float = 0.06
    breakout_buffer_percent: float = 0.005
    max_candidates: int = 3


@dataclass(frozen=True, slots=True)
class DiamondPattern:
    pattern_type: DiamondType
    display_name: str
    start_date: date
    end_date: date
    available_date: date
    pivots: tuple[PricePivot, ...]
    boundary_segments: tuple[BoundaryLine, BoundaryLine, BoundaryLine, BoundaryLine]
    upper_active: BoundaryLine
    lower_active: BoundaryLine
    completion_state: str
    breakout_direction: str | None
    breakout_date: date | None
    invalidation_date: date | None
    score: float
    score_components: dict[str, float]
    context_change_percent: float
    volume_ratio: float | None
    primary: bool = False


def detect_diamond_patterns(
    bars: Sequence[AnalysisBar],
    pivots: Sequence[PricePivot],
    config: DiamondConfig = DiamondConfig(),
) -> tuple[DiamondPattern, ...]:
    ordered = sorted(bars, key=lambda item: item.period_end)
    index_by_date = {bar.period_end: index for index, bar in enumerate(ordered)}
    confirmed = [
        item for item in pivots
        if not item.tentative and item.confirmed_date is not None
        and item.pivot_date in index_by_date
    ]
    candidates: list[DiamondPattern] = []
    for end in range(config.minimum_pivots, len(confirmed) + 1):
        for size in range(config.minimum_pivots, min(config.maximum_pivots, end) + 1):
            candidate = _fit_window(
                ordered, index_by_date, confirmed[end - size:end], config
            )
            if candidate is not None:
                candidates.append(candidate)
    identities: dict[tuple[date, date], DiamondPattern] = {}
    for item in candidates:
        key = (item.start_date, item.end_date)
        if key not in identities or item.score > identities[key].score:
            identities[key] = item
    selected = sorted(identities.values(), key=lambda item: item.score, reverse=True)[
        :config.max_candidates
    ]
    return tuple(_with_primary(item, index == 0) for index, item in enumerate(selected))


def _fit_window(
    bars: Sequence[AnalysisBar],
    index_by_date: dict[date, int],
    pivots: Sequence[PricePivot],
    config: DiamondConfig,
) -> DiamondPattern | None:
    start_index = index_by_date[pivots[0].pivot_date]
    end_index = index_by_date[pivots[-1].pivot_date]
    if end_index - start_index < config.minimum_duration_bars:
        return None
    split = len(pivots) // 2
    broadening = pivots[:split + 1]
    contracting = pivots[max(0, split - 1):]
    broad_highs = [item for item in broadening if item.kind is PivotKind.HIGH]
    broad_lows = [item for item in broadening if item.kind is PivotKind.LOW]
    contract_highs = [item for item in contracting if item.kind is PivotKind.HIGH]
    contract_lows = [item for item in contracting if item.kind is PivotKind.LOW]
    if min(map(len, (broad_highs, broad_lows, contract_highs, contract_lows))) < 2:
        return None
    upper_first = _fit_line(broad_highs, index_by_date)
    lower_first = _fit_line(broad_lows, index_by_date)
    upper_second = _fit_line(contract_highs, index_by_date)
    lower_second = _fit_line(contract_lows, index_by_date)
    scale = max(1e-9, sum(item.price for item in pivots) / len(pivots))
    duration = end_index - start_index
    changes = (
        upper_first[0] * duration / scale,
        lower_first[0] * duration / scale,
        upper_second[0] * duration / scale,
        lower_second[0] * duration / scale,
    )
    threshold = config.minimum_boundary_change_percent
    if not (changes[0] >= threshold and changes[1] <= -threshold
            and changes[2] <= -threshold and changes[3] >= threshold):
        return None
    if max(value[2] for value in (upper_first, lower_first, upper_second, lower_second)) / scale > config.maximum_boundary_residual_percent:
        return None
    middle_index = index_by_date[pivots[split].pivot_date]
    start_width = _price(upper_first, start_index) - _price(lower_first, start_index)
    middle_width = _price(upper_first, middle_index) - _price(lower_first, middle_index)
    end_width = _price(upper_second, end_index) - _price(lower_second, end_index)
    if start_width <= 0 or middle_width <= 0 or end_width <= 0:
        return None
    if middle_width / start_width < config.minimum_expansion_ratio:
        return None
    if end_width / middle_width > config.maximum_end_width_ratio:
        return None
    context_start = max(0, start_index - duration)
    context_close = bars[context_start].close
    context_change = bars[start_index].close / context_close - 1 if context_close else 0.0
    pattern_type = (
        DiamondType.DIAMOND_TOP if context_change >= config.context_trend_percent
        else DiamondType.DIAMOND_BOTTOM if context_change <= -config.context_trend_percent
        else DiamondType.DIAMOND
    )
    available_date = max(item.confirmed_date for item in pivots)
    breakout_direction: str | None = None
    breakout_date: date | None = None
    invalidation_date: date | None = None
    trigger_index: int | None = None
    for index, bar in enumerate(bars):
        if bar.period_end < available_date:
            continue
        upper = _price(upper_second, index)
        lower = _price(lower_second, index)
        if breakout_direction is None:
            if bar.close > upper * (1 + config.breakout_buffer_percent):
                breakout_direction, breakout_date, trigger_index = "up", bar.period_end, index
            elif bar.close < lower * (1 - config.breakout_buffer_percent):
                breakout_direction, breakout_date, trigger_index = "down", bar.period_end, index
        elif (breakout_direction == "up" and bar.close < lower) or (
            breakout_direction == "down" and bar.close > upper
        ):
            invalidation_date = bar.period_end
            break
    expansion_score = min(1.0, (middle_width / start_width - 1) / 0.8)
    contraction_score = min(1.0, (1 - end_width / middle_width) / 0.7)
    residual_score = 1 - min(
        1.0,
        max(value[2] for value in (upper_first, lower_first, upper_second, lower_second))
        / scale / config.maximum_boundary_residual_percent,
    )
    symmetry_score = 1 - min(1.0, abs((middle_index - start_index) - (end_index - middle_index)) / duration)
    score = 0.25 * expansion_score + 0.25 * contraction_score + 0.30 * residual_score + 0.20 * symmetry_score
    return DiamondPattern(
        pattern_type=pattern_type,
        display_name={
            DiamondType.DIAMOND: "菱形",
            DiamondType.DIAMOND_TOP: "菱形顶",
            DiamondType.DIAMOND_BOTTOM: "菱形底",
        }[pattern_type],
        start_date=pivots[0].pivot_date,
        end_date=pivots[-1].pivot_date,
        available_date=available_date,
        pivots=tuple(pivots),
        boundary_segments=(
            _boundary(upper_first, broad_highs, index_by_date),
            _boundary(lower_first, broad_lows, index_by_date),
            _boundary(upper_second, contract_highs, index_by_date),
            _boundary(lower_second, contract_lows, index_by_date),
        ),
        upper_active=_boundary(upper_second, contract_highs, index_by_date),
        lower_active=_boundary(lower_second, contract_lows, index_by_date),
        completion_state="invalidated" if invalidation_date else "confirmed" if breakout_date else "forming",
        breakout_direction=breakout_direction,
        breakout_date=breakout_date,
        invalidation_date=invalidation_date,
        score=round(score, 6),
        score_components={
            "expansion": round(expansion_score, 6),
            "contraction": round(contraction_score, 6),
            "boundary_fit": round(residual_score, 6),
            "symmetry": round(symmetry_score, 6),
        },
        context_change_percent=context_change,
        volume_ratio=_volume_ratio(bars, trigger_index) if trigger_index is not None else None,
    )


def _fit_line(
    pivots: Sequence[PricePivot], index_by_date: dict[date, int]
) -> tuple[float, float, float]:
    xs = [index_by_date[item.pivot_date] for item in pivots]
    ys = [item.price for item in pivots]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    denominator = sum((item - mean_x) ** 2 for item in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
    intercept = mean_y - slope * mean_x
    residual = sqrt(sum((y - _price((slope, intercept, 0), x)) ** 2 for x, y in zip(xs, ys)) / len(xs))
    return slope, intercept, residual


def _price(line: tuple[float, float, float], index: int) -> float:
    return line[1] + line[0] * index


def _boundary(
    line: tuple[float, float, float], pivots: Sequence[PricePivot],
    index_by_date: dict[date, int],
) -> BoundaryLine:
    first, last = pivots[0], pivots[-1]
    return BoundaryLine(
        first.pivot_date, _price(line, index_by_date[first.pivot_date]),
        last.pivot_date, _price(line, index_by_date[last.pivot_date]), line[0],
    )


def _volume_ratio(bars: Sequence[AnalysisBar], index: int) -> float | None:
    prior = [item.volume for item in bars[max(0, index - 20):index] if item.volume > 0]
    if not prior or bars[index].volume <= 0:
        return None
    return bars[index].volume / (sum(prior) / len(prior))


def _with_primary(value: DiamondPattern, primary: bool) -> DiamondPattern:
    return DiamondPattern(
        value.pattern_type, value.display_name, value.start_date, value.end_date,
        value.available_date, value.pivots, value.boundary_segments,
        value.upper_active, value.lower_active, value.completion_state,
        value.breakout_direction, value.breakout_date, value.invalidation_date,
        value.score, value.score_components, value.context_change_percent,
        value.volume_ratio, primary,
    )
