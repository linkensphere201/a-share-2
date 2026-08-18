"""Causal price-volume breakout state transitions for structural boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar


class BreakoutDirection(StrEnum):
    UP = "up"
    DOWN = "down"


class BreakoutState(StrEnum):
    FORMING = "forming"
    READY = "ready"
    TRIGGERED = "triggered"
    CONFIRMED = "confirmed"
    RETESTING = "retesting"
    CONTINUING = "continuing"
    FAILED = "failed"
    INVALIDATED = "invalidated"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class BreakoutConfig:
    trigger_buffer_percent: float = 0.005
    confirmation_distance_percent: float = 0.01
    continuation_distance_percent: float = 0.025
    retest_tolerance_percent: float = 0.012
    minimum_relative_volume: float = 1.2
    minimum_range_expansion: float = 1.1
    minimum_close_location: float = 0.65
    follow_through_bars: int = 3


@dataclass(frozen=True, slots=True)
class BreakoutEvidence:
    trade_date: date
    close: float
    distance_percent: float
    relative_volume: float | None
    range_expansion: float
    close_location: float
    pre_breakout_contraction: float
    adverse_wick_ratio: float


@dataclass(frozen=True, slots=True)
class BreakoutTransition:
    state: BreakoutState
    transition_date: date
    reason: str
    evidence: BreakoutEvidence


@dataclass(frozen=True, slots=True)
class BreakoutEvaluation:
    current_state: BreakoutState
    direction: BreakoutDirection
    boundary_price: float
    invalidation_price: float
    available_date: date
    trigger_date: date | None
    confirmation_date: date | None
    failure_date: date | None
    transitions: tuple[BreakoutTransition, ...]
    preview: bool


def evaluate_breakout(
    bars: Sequence[AnalysisBar],
    *,
    direction: BreakoutDirection,
    boundary_price: float,
    invalidation_price: float,
    available_date: date,
    preview: bool,
    config: BreakoutConfig = BreakoutConfig(),
) -> BreakoutEvaluation:
    ordered = sorted(bars, key=lambda item: item.period_end)
    eligible = [
        (index, bar) for index, bar in enumerate(ordered)
        if bar.period_end >= available_date
    ]
    if not eligible:
        return BreakoutEvaluation(
            BreakoutState.FORMING, direction, boundary_price, invalidation_price,
            available_date, None, None, None, (), preview,
        )
    transitions: list[BreakoutTransition] = []
    current = BreakoutState.READY
    trigger_date: date | None = None
    confirmation_date: date | None = None
    failure_date: date | None = None
    trigger_index: int | None = None
    confirmed_once = False
    for index, bar in eligible:
        evidence = _evidence(ordered, index, direction, boundary_price)
        invalid = (
            bar.close < invalidation_price
            if direction is BreakoutDirection.UP
            else bar.close > invalidation_price
        )
        outside = (
            evidence.distance_percent >= config.trigger_buffer_percent
            if direction is BreakoutDirection.UP
            else evidence.distance_percent <= -config.trigger_buffer_percent
        )
        back_inside = (
            evidence.distance_percent < -config.trigger_buffer_percent
            if direction is BreakoutDirection.UP
            else evidence.distance_percent > config.trigger_buffer_percent
        )
        if invalid and trigger_date is None:
            current = BreakoutState.INVALIDATED
            failure_date = bar.period_end
            transitions.append(BreakoutTransition(
                current, bar.period_end, "structure invalidation level closed through", evidence
            ))
            break
        if trigger_date is None and outside:
            trigger_date = bar.period_end
            trigger_index = index
            current = BreakoutState.TRIGGERED
            transitions.append(BreakoutTransition(
                current, bar.period_end, "close crossed the buffered structural boundary", evidence
            ))
            if _strong_confirmation(evidence, config):
                current = BreakoutState.CONFIRMED
                confirmation_date = bar.period_end
                confirmed_once = True
                transitions.append(BreakoutTransition(
                    current, bar.period_end, "price-volume expansion confirmed the trigger", evidence
                ))
            continue
        if trigger_date is None:
            continue
        if invalid or back_inside:
            current = BreakoutState.FAILED if not invalid else BreakoutState.INVALIDATED
            failure_date = bar.period_end
            transitions.append(BreakoutTransition(
                current,
                bar.period_end,
                "close returned inside the prior structure" if not invalid
                else "structure invalidation level closed through",
                evidence,
            ))
            break
        bars_since_trigger = index - (
            trigger_index if trigger_index is not None else index
        )
        directional_distance = (
            evidence.distance_percent
            if direction is BreakoutDirection.UP else -evidence.distance_percent
        )
        if not confirmed_once:
            if directional_distance >= config.confirmation_distance_percent:
                current = BreakoutState.CONFIRMED
                confirmation_date = bar.period_end
                confirmed_once = True
                transitions.append(BreakoutTransition(
                    current, bar.period_end, "follow-through held beyond the boundary", evidence
                ))
            elif bars_since_trigger > config.follow_through_bars:
                current = BreakoutState.FAILED
                failure_date = bar.period_end
                transitions.append(BreakoutTransition(
                    current, bar.period_end, "trigger had no timely follow-through", evidence
                ))
                break
            continue
        touched_boundary = (
            bar.low <= boundary_price * (1 + config.retest_tolerance_percent)
            if direction is BreakoutDirection.UP
            else bar.high >= boundary_price * (1 - config.retest_tolerance_percent)
        )
        if touched_boundary and outside and current is not BreakoutState.RETESTING:
            current = BreakoutState.RETESTING
            transitions.append(BreakoutTransition(
                current, bar.period_end, "price retested the boundary and closed outside", evidence
            ))
        elif directional_distance >= config.continuation_distance_percent and current is not BreakoutState.CONTINUING:
            current = BreakoutState.CONTINUING
            transitions.append(BreakoutTransition(
                current, bar.period_end, "price extended away from the confirmed boundary", evidence
            ))
    return BreakoutEvaluation(
        current, direction, boundary_price, invalidation_price, available_date,
        trigger_date, confirmation_date, failure_date, tuple(transitions), preview,
    )


def _strong_confirmation(evidence: BreakoutEvidence, config: BreakoutConfig) -> bool:
    return (
        (evidence.relative_volume or 0) >= config.minimum_relative_volume
        and evidence.range_expansion >= config.minimum_range_expansion
        and evidence.close_location >= config.minimum_close_location
    )


def _evidence(
    bars: Sequence[AnalysisBar], index: int,
    direction: BreakoutDirection, boundary: float,
) -> BreakoutEvidence:
    bar = bars[index]
    prior = bars[max(0, index - 20):index]
    prior_volumes = [item.volume for item in prior if item.volume > 0]
    relative_volume = (
        bar.volume / (sum(prior_volumes) / len(prior_volumes))
        if bar.volume > 0 and prior_volumes else None
    )
    bar_range = max(bar.high - bar.low, abs(bar.close) * 1e-6)
    prior_ranges = [max(item.high - item.low, abs(item.close) * 1e-6) for item in prior]
    average_range = sum(prior_ranges) / len(prior_ranges) if prior_ranges else bar_range
    range_expansion = bar_range / average_range
    close_location = (
        (bar.close - bar.low) / bar_range
        if direction is BreakoutDirection.UP
        else (bar.high - bar.close) / bar_range
    )
    recent = prior_ranges[-5:]
    older = prior_ranges[-20:-5]
    contraction = (
        (sum(recent) / len(recent)) / (sum(older) / len(older))
        if recent and older else 1.0
    )
    adverse_wick = (
        bar.high - max(bar.open, bar.close)
        if direction is BreakoutDirection.UP
        else min(bar.open, bar.close) - bar.low
    )
    return BreakoutEvidence(
        trade_date=bar.period_end,
        close=bar.close,
        distance_percent=(bar.close / boundary - 1),
        relative_volume=relative_volume,
        range_expansion=range_expansion,
        close_location=max(0.0, min(1.0, close_location)),
        pre_breakout_contraction=contraction,
        adverse_wick_ratio=max(0.0, adverse_wick / bar_range),
    )
