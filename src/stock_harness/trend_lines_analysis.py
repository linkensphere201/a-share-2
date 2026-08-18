"""Causal support and resistance lines fitted from confirmed price pivots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from itertools import combinations
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.trend_pivots import PivotKind, PricePivot


class TrendHorizon(StrEnum):
    SHORT = "short"
    LONG = "long"


class TrendLineKind(StrEnum):
    SUPPORT = "support"
    RESISTANCE = "resistance"


@dataclass(frozen=True, slots=True)
class TrendLineConfig:
    min_anchor_span_bars: int = 4
    max_pivots_per_kind: int = 12
    max_lines_per_kind: int = 3
    touch_tolerance_percent: float = 0.012
    body_tolerance_percent: float = 0.004
    max_body_cross_ratio: float = 0.12
    max_penetrations: int = 2
    duplicate_price_percent: float = 0.012

    def validate(self) -> None:
        if self.min_anchor_span_bars < 2:
            raise ValueError("minimum anchor span must be at least two bars")
        if not 2 <= self.max_pivots_per_kind <= 50:
            raise ValueError("maximum pivots per kind must be between 2 and 50")
        if not 1 <= self.max_lines_per_kind <= 10:
            raise ValueError("maximum lines per kind must be between 1 and 10")
        if not 0 < self.touch_tolerance_percent <= 0.1:
            raise ValueError("touch tolerance must be between 0 and 10%")
        if not 0 <= self.body_tolerance_percent <= 0.05:
            raise ValueError("body tolerance must be between 0 and 5%")
        if not 0 <= self.max_body_cross_ratio <= 0.5:
            raise ValueError("body-cross ratio must be between 0 and 50%")
        if self.max_penetrations < 0:
            raise ValueError("maximum penetrations cannot be negative")


@dataclass(frozen=True, slots=True)
class TrendLineCandidate:
    kind: TrendLineKind
    horizon: TrendHorizon
    first_pivot_date: date
    first_price: float
    first_confirmed_date: date
    second_pivot_date: date
    second_price: float
    second_confirmed_date: date
    available_date: date
    slope_per_bar: float
    projected_price: float
    touch_count: int
    penetration_count: int
    body_cross_count: int
    evaluated_bar_count: int
    score: float
    score_components: dict[str, float]
    invalidation_reason: str | None


def generate_trend_line_candidates(
    bars: Sequence[AnalysisBar],
    pivots: Sequence[PricePivot],
    horizon: TrendHorizon,
    config: TrendLineConfig = TrendLineConfig(),
) -> tuple[TrendLineCandidate, ...]:
    """Fit active lines from confirmed same-kind pivots using only supplied bars."""
    config.validate()
    if len(bars) < config.min_anchor_span_bars + 1:
        return ()
    ordered = sorted(bars, key=lambda item: item.period_end)
    index_by_date = {bar.period_end: index for index, bar in enumerate(ordered)}
    candidates: list[TrendLineCandidate] = []
    for pivot_kind, line_kind in (
        (PivotKind.LOW, TrendLineKind.SUPPORT),
        (PivotKind.HIGH, TrendLineKind.RESISTANCE),
    ):
        eligible = [
            pivot for pivot in pivots
            if pivot.kind is pivot_kind
            and not pivot.tentative
            and pivot.confirmed_date is not None
            and pivot.pivot_date in index_by_date
        ][-config.max_pivots_per_kind:]
        for first, second in combinations(eligible, 2):
            fitted = _fit_candidate(
                ordered, index_by_date, first, second, line_kind, horizon, config
            )
            if fitted is not None:
                candidates.append(fitted)

    candidates.sort(key=lambda item: item.score, reverse=True)
    selected: list[TrendLineCandidate] = []
    counts = {TrendLineKind.SUPPORT: 0, TrendLineKind.RESISTANCE: 0}
    for candidate in candidates:
        if counts[candidate.kind] >= config.max_lines_per_kind:
            continue
        if any(_duplicates(candidate, existing, config) for existing in selected):
            continue
        selected.append(candidate)
        counts[candidate.kind] += 1
    return tuple(selected)


def _fit_candidate(
    bars: Sequence[AnalysisBar],
    index_by_date: dict[date, int],
    first: PricePivot,
    second: PricePivot,
    kind: TrendLineKind,
    horizon: TrendHorizon,
    config: TrendLineConfig,
) -> TrendLineCandidate | None:
    first_index = index_by_date[first.pivot_date]
    second_index = index_by_date[second.pivot_date]
    span = second_index - first_index
    if span < config.min_anchor_span_bars:
        return None
    slope = (second.price - first.price) / span
    touches = penetrations = body_crosses = 0
    violation_residual = 0.0
    evaluated = bars[first_index:]
    for offset, bar in enumerate(evaluated):
        index = first_index + offset
        line_price = first.price + slope * (index - first_index)
        touch_tolerance = max(abs(line_price) * config.touch_tolerance_percent, 1e-9)
        body_tolerance = max(abs(line_price) * config.body_tolerance_percent, 1e-9)
        observed_edge = bar.low if kind is TrendLineKind.SUPPORT else bar.high
        if abs(observed_edge - line_price) <= touch_tolerance:
            touches += 1
        if kind is TrendLineKind.SUPPORT:
            penetrations += int(bar.low < line_price - touch_tolerance)
            body_crosses += int(min(bar.open, bar.close) < line_price - body_tolerance)
            violation_residual += max(0.0, line_price - bar.low) / touch_tolerance
        else:
            penetrations += int(bar.high > line_price + touch_tolerance)
            body_crosses += int(max(bar.open, bar.close) > line_price + body_tolerance)
            violation_residual += max(0.0, bar.high - line_price) / touch_tolerance

    evaluated_count = len(evaluated)
    body_cross_ratio = body_crosses / evaluated_count
    if penetrations > config.max_penetrations or body_cross_ratio > config.max_body_cross_ratio:
        return None
    recency = 1 - ((len(bars) - 1 - second_index) / max(1, len(bars) - 1))
    span_score = min(1.0, span / max(10, len(bars) * 0.3))
    touch_score = min(1.0, max(0, touches - 2) / 3)
    integrity = 1 - body_cross_ratio
    residual_score = 1 - min(1.0, violation_residual / evaluated_count / 3)
    penetration_score = 1 - penetrations / max(1, config.max_penetrations + 1)
    score = (
        0.25 * span_score
        + 0.20 * touch_score
        + 0.15 * recency
        + 0.15 * integrity
        + 0.15 * residual_score
        + 0.10 * penetration_score
    )
    projected = first.price + slope * (len(bars) - 1 - first_index)
    return TrendLineCandidate(
        kind=kind,
        horizon=horizon,
        first_pivot_date=first.pivot_date,
        first_price=first.price,
        first_confirmed_date=first.confirmed_date,
        second_pivot_date=second.pivot_date,
        second_price=second.price,
        second_confirmed_date=second.confirmed_date,
        available_date=max(first.confirmed_date, second.confirmed_date),
        slope_per_bar=slope,
        projected_price=projected,
        touch_count=touches,
        penetration_count=penetrations,
        body_cross_count=body_crosses,
        evaluated_bar_count=evaluated_count,
        score=round(score, 6),
        score_components={
            "span": round(span_score, 6),
            "touches": round(touch_score, 6),
            "recency": round(recency, 6),
            "integrity": round(integrity, 6),
            "residual": round(residual_score, 6),
            "penetration": round(penetration_score, 6),
        },
        invalidation_reason=None,
    )


def _duplicates(
    candidate: TrendLineCandidate,
    existing: TrendLineCandidate,
    config: TrendLineConfig,
) -> bool:
    if candidate.kind is not existing.kind or candidate.horizon is not existing.horizon:
        return False
    scale = max(abs(candidate.projected_price), abs(existing.projected_price), 1e-9)
    return abs(candidate.projected_price - existing.projected_price) / scale <= config.duplicate_price_percent
