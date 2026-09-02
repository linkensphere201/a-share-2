"""Causal major descending-resistance lines shared by screening and chart analysis."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar


class MajorLinePeriod(StrEnum):
    QUARTER = "3m"
    HALF_YEAR = "6m"
    YEAR = "1y"


class MajorLineState(StrEnum):
    CRITICAL_BREAKOUT = "critical-breakout"
    BREAKOUT_RETEST = "breakout-retest"
    BROKEN_OUT = "broken-out"


@dataclass(frozen=True, slots=True)
class MajorLineProfile:
    period: MajorLinePeriod
    bars: int
    pivot_radius: int
    minimum_anchor_span: int
    minimum_decline_percent: float


PROFILES = (
    MajorLineProfile(MajorLinePeriod.QUARTER, 60, 3, 18, 4.0),
    MajorLineProfile(MajorLinePeriod.HALF_YEAR, 120, 5, 40, 6.0),
    MajorLineProfile(MajorLinePeriod.YEAR, 250, 8, 80, 10.0),
)


@dataclass(frozen=True, slots=True)
class MajorDescendingLine:
    item_id: str
    code: str
    period: MajorLinePeriod
    state: MajorLineState
    first_index: int
    second_index: int
    first_date: str
    second_date: str
    first_price: float
    second_price: float
    slope_per_bar: float
    projected_price: float
    distance_percent: float
    touch_count: int
    penetration_count: int
    anchor_span_bars: int
    decline_percent: float
    breakout_date: str | None
    volume_ratio_5: float | None
    support_price: float
    invalidation_price: float
    first_target_price: float
    major_target_price: float
    first_risk_reward: float | None
    major_risk_reward: float | None
    score: float


def detect_major_descending_lines(
    bars: Sequence[AnalysisBar],
    periods: Sequence[MajorLinePeriod] | None = None,
) -> tuple[MajorDescendingLine, ...]:
    """Return active lines using only bars available at the supplied cutoff."""
    requested = set(periods or tuple(item.period for item in PROFILES))
    results: list[MajorDescendingLine] = []
    for profile in PROFILES:
        if profile.period not in requested or len(bars) < profile.bars:
            continue
        results.extend(_detect_profile(tuple(bars[-profile.bars:]), profile))
    results.sort(key=lambda item: (_state_rank(item.state), item.score), reverse=True)
    numbered: list[MajorDescendingLine] = []
    counts: dict[MajorLinePeriod, int] = {}
    for item in results:
        counts[item.period] = counts.get(item.period, 0) + 1
        numbered.append(_replace_code(item, f"MDL-{item.period.value.upper()}-{counts[item.period]:02d}"))
    return tuple(numbered)


def _detect_profile(
    bars: tuple[AnalysisBar, ...], profile: MajorLineProfile
) -> list[MajorDescendingLine]:
    highs = [item.high for item in bars]
    pivot_indexes = _confirmed_high_indexes(highs, profile.pivot_radius)
    candidates: list[MajorDescendingLine] = []
    for first, second in combinations(pivot_indexes, 2):
        span = second - first
        if span < profile.minimum_anchor_span or highs[second] >= highs[first] * 0.997:
            continue
        slope = (highs[second] - highs[first]) / span
        decline = (highs[second] / highs[first] - 1) * 100
        if slope >= 0 or decline > -profile.minimum_decline_percent:
            continue
        projected = highs[first] + slope * (len(bars) - 1 - first)
        if projected <= 0:
            continue
        touches = sum(
            abs(highs[index] - _line_price(highs[first], slope, first, index))
            / max(_line_price(highs[first], slope, first, index), 1e-9) <= 0.025
            for index in pivot_indexes if first <= index <= len(bars) - profile.pivot_radius - 1
        )
        penetrations = sum(
            highs[index] > _line_price(highs[first], slope, first, index) * 1.035
            for index in range(second + 1, max(second + 1, len(bars) - 3))
        )
        if touches < 2 or penetrations > 1:
            continue
        state, breakout_index = _classify_state(bars, first, slope)
        if state is None:
            continue
        latest = bars[-1]
        distance = (latest.close / projected - 1) * 100
        support = min(item.low for item in bars[-14:])
        invalidation = support * 0.99
        overhead = sorted({
            item.high for item in bars[-120:-1] if item.high > latest.close * 1.01
        })
        first_target = overhead[0] if overhead else max(highs[second], latest.close)
        major_target = max(highs[first], latest.close)
        risk = latest.close - invalidation
        first_rr = _risk_reward(latest.close, invalidation, first_target)
        major_rr = _risk_reward(latest.close, invalidation, major_target)
        volume_ratio = _volume_ratio_5(bars)
        recency = 1 - (len(bars) - 1 - second) / max(1, len(bars) - 1)
        score = (
            2.0 * min(touches, 6)
            + 0.2 * min(abs(decline), 35)
            + min(span / profile.minimum_anchor_span, 3)
            + 1.5 * recency
            + 1.2 * min(volume_ratio or 0, 2)
            + _state_rank(state)
            - 2.0 * penetrations
            - 0.4 * abs(distance)
        )
        identity = (
            f"major-descending-{profile.period.value}-"
            f"{bars[first].period_end:%Y%m%d}-{bars[second].period_end:%Y%m%d}"
        )
        candidates.append(MajorDescendingLine(
            item_id=identity,
            code="",
            period=profile.period,
            state=state,
            first_index=first,
            second_index=second,
            first_date=bars[first].period_end.isoformat(),
            second_date=bars[second].period_end.isoformat(),
            first_price=highs[first],
            second_price=highs[second],
            slope_per_bar=slope,
            projected_price=projected,
            distance_percent=distance,
            touch_count=touches,
            penetration_count=penetrations,
            anchor_span_bars=span,
            decline_percent=decline,
            breakout_date=(bars[breakout_index].period_end.isoformat() if breakout_index is not None else None),
            volume_ratio_5=volume_ratio,
            support_price=support,
            invalidation_price=invalidation,
            first_target_price=first_target,
            major_target_price=major_target,
            first_risk_reward=first_rr,
            major_risk_reward=major_rr,
            score=round(score, 6),
        ))
    candidates.sort(key=lambda item: item.score, reverse=True)
    return _deduplicate(candidates)[:3]


def _confirmed_high_indexes(values: Sequence[float], radius: int) -> list[int]:
    indexes: list[int] = []
    for index in range(radius, len(values) - radius):
        window = values[index - radius:index + radius + 1]
        if values[index] == max(window) and window.count(values[index]) == 1:
            indexes.append(index)
    return indexes


def _classify_state(
    bars: Sequence[AnalysisBar], first: int, slope: float
) -> tuple[MajorLineState | None, int | None]:
    first_price = bars[first].high
    boundaries = [_line_price(first_price, slope, first, index) for index in range(len(bars))]
    latest_distance = bars[-1].close / boundaries[-1] - 1
    breakout_indexes = [
        index for index in range(max(first + 1, len(bars) - 8), len(bars))
        if bars[index].close > boundaries[index] * 1.005
        and bars[index - 1].close <= boundaries[index - 1] * 1.005
    ]
    if breakout_indexes:
        breakout = breakout_indexes[-1]
        touched = any(
            bars[index].low <= boundaries[index] * 1.012
            and bars[index].close >= boundaries[index] * 0.997
            for index in range(breakout + 1, len(bars))
        )
        if touched and abs(latest_distance) <= 0.012:
            return MajorLineState.BREAKOUT_RETEST, breakout
        if latest_distance >= 0.005:
            return MajorLineState.BROKEN_OUT, breakout
        return None, breakout
    if -0.02 <= latest_distance <= 0.005:
        return MajorLineState.CRITICAL_BREAKOUT, None
    return None, None


def _deduplicate(values: Sequence[MajorDescendingLine]) -> list[MajorDescendingLine]:
    selected: list[MajorDescendingLine] = []
    for value in values:
        if any(
            item.period is value.period
            and abs(item.projected_price - value.projected_price)
            / max(item.projected_price, value.projected_price, 1e-9) <= 0.012
            for item in selected
        ):
            continue
        selected.append(value)
    return selected


def _replace_code(value: MajorDescendingLine, code: str) -> MajorDescendingLine:
    return MajorDescendingLine(**{
        field: (code if field == "code" else getattr(value, field))
        for field in value.__dataclass_fields__
    })


def _line_price(first_price: float, slope: float, first: int, index: int) -> float:
    return first_price + slope * (index - first)


def _volume_ratio_5(bars: Sequence[AnalysisBar]) -> float | None:
    if len(bars) < 6:
        return None
    average = sum(item.volume for item in bars[-6:-1]) / 5
    return bars[-1].volume / average if average > 0 else None


def _risk_reward(entry: float, stop: float, target: float) -> float | None:
    risk = entry - stop
    reward = target - entry
    return round(reward / risk, 6) if risk > 0 and reward > 0 else None


def _state_rank(state: MajorLineState) -> int:
    return {
        MajorLineState.CRITICAL_BREAKOUT: 1,
        MajorLineState.BREAKOUT_RETEST: 3,
        MajorLineState.BROKEN_OUT: 2,
    }[state]
