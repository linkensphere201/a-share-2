"""Canonical geometry, envelope, and evolution rules for generated trend lines."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.trend_line_envelope import (
    EnvelopeIntegrity,
    EnvelopePolicy,
    EnvelopeSide,
    evaluate_trend_line_envelope,
)


class TrendSpeedState(StrEnum):
    ACCELERATING = "accelerating"
    STABLE = "stable"
    DECELERATING = "decelerating"
    FLATTENING = "flattening"


@dataclass(frozen=True, slots=True)
class StandardTrendLine:
    side: EnvelopeSide
    first_index: int
    second_index: int
    first_price: float
    second_price: float
    slope_per_bar: float
    log_slope_per_20: float
    projected_price: float
    integrity: EnvelopeIntegrity


@dataclass(frozen=True, slots=True)
class TrendLineEvolution:
    previous_second_index: int
    previous_second_price: float
    previous_slope_per_bar: float
    previous_log_slope_per_20: float
    slope_change_ratio: float
    speed_state: TrendSpeedState


def evaluate_standard_trend_line(
    bars: Sequence[AnalysisBar],
    atrs: Sequence[float],
    first_index: int,
    second_index: int,
    evaluation_end_index: int,
    contact_indexes: Sequence[int],
    independent_separation_bars: int,
    side: EnvelopeSide,
    policy: EnvelopePolicy = EnvelopePolicy(),
) -> StandardTrendLine:
    """Fit one anchor pair and evaluate every bar through the causal cutoff."""
    if not 0 <= first_index < second_index < len(bars):
        raise ValueError("trend-line anchors must be ordered indexes inside bars")
    if not second_index <= evaluation_end_index < len(bars):
        raise ValueError("trend-line evaluation end must include the second anchor")
    first_price = _extreme(bars[first_index], side)
    second_price = _extreme(bars[second_index], side)
    slope = (second_price - first_price) / (second_index - first_index)
    projected = first_price + slope * (len(bars) - 1 - first_index)
    integrity = evaluate_trend_line_envelope(
        bars, atrs, first_index, second_index, first_price, slope,
        evaluation_end_index, contact_indexes, independent_separation_bars,
        side, policy,
    )
    return StandardTrendLine(
        side=side,
        first_index=first_index,
        second_index=second_index,
        first_price=first_price,
        second_price=second_price,
        slope_per_bar=slope,
        log_slope_per_20=_log_slope(first_price, second_price, second_index - first_index),
        projected_price=projected,
        integrity=integrity,
    )


def compare_reanchored_lines(
    first_price: float,
    previous_second_index: int,
    previous_second_price: float,
    current_second_index: int,
    current_second_price: float,
    *,
    first_index: int,
    stable_ratio: float = 0.15,
    flattening_log_slope_per_20: float = 0.005,
) -> TrendLineEvolution:
    """Compare two same-origin lines without depending on chart pixel angles."""
    previous_span = previous_second_index - first_index
    current_span = current_second_index - first_index
    if previous_span <= 0 or current_span <= previous_span:
        raise ValueError("reanchored trend lines require a later current anchor")
    previous_slope = (previous_second_price - first_price) / previous_span
    current_slope = (current_second_price - first_price) / current_span
    previous_log = _log_slope(first_price, previous_second_price, previous_span)
    current_log = _log_slope(first_price, current_second_price, current_span)
    previous_speed = abs(previous_log)
    current_speed = abs(current_log)
    ratio = (
        (current_speed - previous_speed) / previous_speed
        if previous_speed > 1e-12 else 0.0
    )
    if current_speed <= flattening_log_slope_per_20:
        state = TrendSpeedState.FLATTENING
    elif ratio > stable_ratio:
        state = TrendSpeedState.ACCELERATING
    elif ratio < -stable_ratio:
        state = TrendSpeedState.DECELERATING
    else:
        state = TrendSpeedState.STABLE
    return TrendLineEvolution(
        previous_second_index=previous_second_index,
        previous_second_price=previous_second_price,
        previous_slope_per_bar=previous_slope,
        previous_log_slope_per_20=previous_log,
        slope_change_ratio=ratio,
        speed_state=state,
    )


def _extreme(bar: AnalysisBar, side: EnvelopeSide) -> float:
    return bar.high if side is EnvelopeSide.UPPER else bar.low


def _log_slope(first: float, second: float, span: int) -> float:
    if first <= 0 or second <= 0 or span <= 0:
        return 0.0
    return math.log(second / first) / span * 20
