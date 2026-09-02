"""Causal support and resistance lines fitted from confirmed price pivots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from itertools import combinations
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.trend_line_envelope import (
    EnvelopePolicy,
    EnvelopeSide,
    dominates_prior_extremes,
    evaluate_trend_line_envelope,
)
from stock_harness.trend_pivots import (
    PivotKind, PricePivot, causal_average_true_range,
)


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
    duplicate_price_percent: float = 0.012
    dominance_lookback_bars: int = 40
    recent_event_bars: int = 3

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
        if self.dominance_lookback_bars < 1:
            raise ValueError("dominance lookback must be positive")
        if not 0 <= self.recent_event_bars <= 10:
            raise ValueError("recent event bars must be between zero and ten")


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
    independent_touch_count: int
    penetration_count: int
    body_cross_count: int
    close_breach_count: int
    maximum_wick_breach_percent: float
    maximum_body_breach_percent: float
    maximum_close_breach_percent: float
    maximum_breach_atr: float
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
    atrs = causal_average_true_range(ordered)
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
                ordered, atrs, index_by_date, eligible, first, second,
                line_kind, horizon, config,
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
    atrs: Sequence[float],
    index_by_date: dict[date, int],
    eligible_pivots: Sequence[PricePivot],
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
    side = EnvelopeSide.LOWER if kind is TrendLineKind.SUPPORT else EnvelopeSide.UPPER
    if not dominates_prior_extremes(
        bars, first_index, config.dominance_lookback_bars,
        atrs[max(0, first_index - 1)], side,
    ):
        return None
    slope = (second.price - first.price) / span
    policy = EnvelopePolicy(
        wick_breach_percent=config.touch_tolerance_percent * 100,
        close_breach_percent=config.body_tolerance_percent * 100,
    )
    event_index = _recent_close_event_index(
        bars, atrs, first_index, first.price, slope, side,
        config.recent_event_bars, policy,
    )
    if event_index is not None and second_index >= event_index:
        return None
    evaluation_end = event_index - 1 if event_index is not None else len(bars) - 1
    contact_indexes = [index_by_date[item.pivot_date] for item in eligible_pivots]
    envelope = evaluate_trend_line_envelope(
        bars, atrs, first_index, second_index, first.price, slope,
        evaluation_end, contact_indexes,
        max(1, config.min_anchor_span_bars // 2), side,
        policy,
    )
    if (
        envelope.wick_breach_count
        or envelope.body_breach_count
        or envelope.close_breach_count
        or envelope.dominant_extreme_count
        or envelope.independent_touch_count < 1
    ):
        return None
    evaluated_count = evaluation_end - first_index + 1
    body_cross_ratio = envelope.body_breach_count / evaluated_count
    recency = 1 - ((len(bars) - 1 - second_index) / max(1, len(bars) - 1))
    span_score = min(1.0, span / max(10, len(bars) * 0.3))
    touch_score = min(1.0, envelope.independent_touch_count / 3)
    integrity = 1 - body_cross_ratio
    residual_score = 1 - min(1.0, envelope.maximum_breach_atr / 3)
    penetration_score = 1.0
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
        touch_count=envelope.touch_count,
        independent_touch_count=envelope.independent_touch_count,
        penetration_count=envelope.wick_breach_count,
        body_cross_count=envelope.body_breach_count,
        close_breach_count=envelope.close_breach_count,
        maximum_wick_breach_percent=envelope.maximum_wick_breach_percent,
        maximum_body_breach_percent=envelope.maximum_body_breach_percent,
        maximum_close_breach_percent=envelope.maximum_close_breach_percent,
        maximum_breach_atr=envelope.maximum_breach_atr,
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


def _recent_close_event_index(
    bars: Sequence[AnalysisBar],
    atrs: Sequence[float],
    first_index: int,
    first_price: float,
    slope: float,
    side: EnvelopeSide,
    recent_event_bars: int,
    policy: EnvelopePolicy,
) -> int | None:
    if recent_event_bars <= 0:
        return None
    start = max(first_index + 1, len(bars) - recent_event_bars)
    for index in range(start, len(bars)):
        boundary = first_price + slope * (index - first_index)
        previous_boundary = first_price + slope * (index - 1 - first_index)
        current_excess = _close_event_excess(bars[index].close, boundary, side)
        previous_excess = _close_event_excess(
            bars[index - 1].close, previous_boundary, side
        )
        threshold = max(
            boundary * policy.close_breach_percent / 100,
            atrs[max(0, index - 1)] * policy.close_breach_atr,
        )
        previous_threshold = max(
            previous_boundary * policy.close_breach_percent / 100,
            atrs[max(0, index - 2)] * policy.close_breach_atr,
        )
        if current_excess > threshold and previous_excess <= previous_threshold:
            return index
    return None


def _close_event_excess(
    close: float, boundary: float, side: EnvelopeSide
) -> float:
    if side is EnvelopeSide.UPPER:
        return max(0.0, close - boundary)
    return max(0.0, boundary - close)


def _duplicates(
    candidate: TrendLineCandidate,
    existing: TrendLineCandidate,
    config: TrendLineConfig,
) -> bool:
    if candidate.kind is not existing.kind or candidate.horizon is not existing.horizon:
        return False
    scale = max(abs(candidate.projected_price), abs(existing.projected_price), 1e-9)
    return abs(candidate.projected_price - existing.projected_price) / scale <= config.duplicate_price_percent
