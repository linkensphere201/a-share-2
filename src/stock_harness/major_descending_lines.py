"""Causal major descending-resistance lines shared by screening and chart analysis."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.trend_line_envelope import (
    EnvelopeIntegrity,
    EnvelopePolicy,
    EnvelopeSide,
    dominates_prior_extremes,
    evaluate_trend_line_envelope,
)
from stock_harness.trade_scenarios import TradeDirection, calculate_risk_reward
from stock_harness.trend_pivots import causal_average_true_range


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
    maximum_anchor_span: int
    minimum_decline_percent: float
    minimum_prominence_percent: float
    dominance_lookback: int


PROFILES = (
    MajorLineProfile(MajorLinePeriod.HALF_YEAR, 250, 5, 100, 179, 6.0, 4.0, 40),
    MajorLineProfile(MajorLinePeriod.YEAR, 250, 8, 180, 240, 10.0, 6.0, 60),
)

ATR_PERIOD = 14
ENVELOPE_POLICY = EnvelopePolicy()


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
    independent_touch_count: int
    penetration_count: int
    wick_breach_count: int
    body_breach_count: int
    close_breach_count: int
    maximum_wick_breach_percent: float
    maximum_body_breach_percent: float
    maximum_close_breach_percent: float
    maximum_breach_atr: float
    first_prominence_percent: float
    second_prominence_percent: float
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


@dataclass(slots=True)
class MajorLineDiagnostics:
    important_pivots: int = 0
    anchor_pairs: int = 0
    rejected_span: int = 0
    rejected_geometry: int = 0
    rejected_dominance: int = 0
    rejected_inactive: int = 0
    rejected_event_order: int = 0
    rejected_wick_breach: int = 0
    rejected_body_breach: int = 0
    rejected_close_breach: int = 0
    rejected_dominant_high: int = 0
    rejected_confirmation: int = 0
    accepted: int = 0

    def merge(self, other: "MajorLineDiagnostics") -> None:
        for field in self.__dataclass_fields__:
            setattr(self, field, getattr(self, field) + getattr(other, field))


def detect_major_descending_lines(
    bars: Sequence[AnalysisBar],
    periods: Sequence[MajorLinePeriod] | None = None,
    *,
    diagnostics: MajorLineDiagnostics | None = None,
) -> tuple[MajorDescendingLine, ...]:
    """Return active lines using only bars available at the supplied cutoff."""
    requested = set(periods or tuple(item.period for item in PROFILES))
    results: list[MajorDescendingLine] = []
    for profile in PROFILES:
        minimum_required = (
            profile.minimum_anchor_span + profile.pivot_radius * 2 + 4
        )
        if profile.period not in requested or len(bars) < minimum_required:
            continue
        results.extend(_detect_profile(
            tuple(bars[-profile.bars:]), profile, diagnostics
        ))
    results.sort(key=lambda item: (_state_rank(item.state), item.score), reverse=True)
    numbered: list[MajorDescendingLine] = []
    counts: dict[MajorLinePeriod, int] = {}
    for item in results:
        counts[item.period] = counts.get(item.period, 0) + 1
        numbered.append(_replace_code(item, f"MDL-{item.period.value.upper()}-{counts[item.period]:02d}"))
    return tuple(numbered)


def _detect_profile(
    bars: tuple[AnalysisBar, ...],
    profile: MajorLineProfile,
    diagnostics: MajorLineDiagnostics | None,
) -> list[MajorDescendingLine]:
    highs = [item.high for item in bars]
    atrs = causal_average_true_range(bars, ATR_PERIOD)
    pivot_indexes = _confirmed_high_indexes(highs, profile.pivot_radius)
    important_indexes = [
        index for index in pivot_indexes
        if _high_prominence_percent(bars, index, profile.pivot_radius)
        >= profile.minimum_prominence_percent
    ]
    if diagnostics is not None:
        diagnostics.important_pivots += len(important_indexes)
    candidates: list[MajorDescendingLine] = []
    for first, second in combinations(important_indexes, 2):
        if diagnostics is not None:
            diagnostics.anchor_pairs += 1
        span = second - first
        if not profile.minimum_anchor_span <= span <= profile.maximum_anchor_span:
            if diagnostics is not None:
                diagnostics.rejected_span += 1
            continue
        if highs[second] >= highs[first] * 0.997:
            if diagnostics is not None:
                diagnostics.rejected_geometry += 1
            continue
        if not dominates_prior_extremes(
            bars, first, profile.dominance_lookback,
            atrs[max(0, first - 1)],
            EnvelopeSide.UPPER,
        ):
            if diagnostics is not None:
                diagnostics.rejected_dominance += 1
            continue
        slope = (highs[second] - highs[first]) / span
        decline = (highs[second] / highs[first] - 1) * 100
        if slope >= 0 or decline > -profile.minimum_decline_percent:
            if diagnostics is not None:
                diagnostics.rejected_geometry += 1
            continue
        projected = highs[first] + slope * (len(bars) - 1 - first)
        if projected <= 0:
            if diagnostics is not None:
                diagnostics.rejected_geometry += 1
            continue
        state, breakout_index = _classify_state(bars, first, slope)
        if state is None:
            if diagnostics is not None:
                diagnostics.rejected_inactive += 1
            continue
        if breakout_index is not None and second >= breakout_index:
            if diagnostics is not None:
                diagnostics.rejected_event_order += 1
            continue
        pre_event_end = (breakout_index - 1) if breakout_index is not None else len(bars) - 1
        integrity = evaluate_trend_line_envelope(
            bars, atrs, first, second, highs[first], slope, pre_event_end, pivot_indexes,
            profile.pivot_radius * 2, EnvelopeSide.UPPER, ENVELOPE_POLICY,
        )
        rejected = False
        if integrity.wick_breach_count:
            rejected = True
            if diagnostics is not None:
                diagnostics.rejected_wick_breach += 1
        if integrity.body_breach_count:
            rejected = True
            if diagnostics is not None:
                diagnostics.rejected_body_breach += 1
        if integrity.close_breach_count:
            rejected = True
            if diagnostics is not None:
                diagnostics.rejected_close_breach += 1
        if integrity.dominant_extreme_count:
            rejected = True
            if diagnostics is not None:
                diagnostics.rejected_dominant_high += 1
        if integrity.independent_touch_count < 1:
            rejected = True
            if diagnostics is not None:
                diagnostics.rejected_confirmation += 1
        if rejected:
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
        first_rr = calculate_risk_reward(
            TradeDirection.LONG, latest.close, invalidation, first_target
        )
        major_rr = calculate_risk_reward(
            TradeDirection.LONG, latest.close, invalidation, major_target
        )
        volume_ratio = _volume_ratio_5(bars)
        recency = 1 - (len(bars) - 1 - second) / max(1, len(bars) - 1)
        score = (
            2.0 * min(integrity.touch_count, 6)
            + 0.2 * min(abs(decline), 35)
            + min(span / profile.minimum_anchor_span, 3)
            + 1.5 * recency
            + 1.2 * min(volume_ratio or 0, 2)
            + _state_rank(state)
            + 1.5 * min(integrity.independent_touch_count, 3)
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
            touch_count=integrity.touch_count,
            independent_touch_count=integrity.independent_touch_count,
            penetration_count=integrity.wick_breach_count,
            wick_breach_count=integrity.wick_breach_count,
            body_breach_count=integrity.body_breach_count,
            close_breach_count=integrity.close_breach_count,
            maximum_wick_breach_percent=integrity.maximum_wick_breach_percent,
            maximum_body_breach_percent=integrity.maximum_body_breach_percent,
            maximum_close_breach_percent=integrity.maximum_close_breach_percent,
            maximum_breach_atr=integrity.maximum_breach_atr,
            first_prominence_percent=_high_prominence_percent(
                bars, first, profile.pivot_radius
            ),
            second_prominence_percent=_high_prominence_percent(
                bars, second, profile.pivot_radius
            ),
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
        if diagnostics is not None:
            diagnostics.accepted += 1
    candidates.sort(key=lambda item: item.score, reverse=True)
    return _deduplicate(candidates)[:3]


def _upper_envelope_integrity(
    bars: Sequence[AnalysisBar],
    atrs: Sequence[float],
    first: int,
    second: int,
    slope: float,
    pre_event_end: int,
    pivot_indexes: Sequence[int],
    pivot_radius: int,
) -> EnvelopeIntegrity:
    return evaluate_trend_line_envelope(
        bars, atrs, first, second, bars[first].high, slope, pre_event_end,
        pivot_indexes, pivot_radius * 2, EnvelopeSide.UPPER, ENVELOPE_POLICY,
    )


def _high_prominence_percent(
    bars: Sequence[AnalysisBar], index: int, radius: int
) -> float:
    wing = radius * 3
    left = bars[max(0, index - wing):index]
    right = bars[index + 1:min(len(bars), index + wing + 1)]
    if not left or not right:
        return 0.0
    shoulder_floor = max(
        min(item.low for item in left),
        min(item.low for item in right),
    )
    if shoulder_floor <= 0:
        return 0.0
    return (bars[index].high / shoulder_floor - 1) * 100


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
    atrs = causal_average_true_range(bars, ATR_PERIOD)
    boundaries = [_line_price(first_price, slope, first, index) for index in range(len(bars))]
    latest_distance = bars[-1].close / boundaries[-1] - 1
    breakout_indexes = [
        index for index in range(max(first + 1, len(bars) - 8), len(bars))
        if _close_above_boundary(bars, atrs, boundaries, index)
        and not _close_above_boundary(bars, atrs, boundaries, index - 1)
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
        if _close_above_boundary(bars, atrs, boundaries, len(bars) - 1):
            return MajorLineState.BROKEN_OUT, breakout
        return None, breakout
    if -0.02 <= latest_distance <= 0.005:
        return MajorLineState.CRITICAL_BREAKOUT, None
    return None, None


def _close_above_boundary(
    bars: Sequence[AnalysisBar],
    atrs: Sequence[float],
    boundaries: Sequence[float],
    index: int,
) -> bool:
    boundary = boundaries[index]
    prior_atr = atrs[max(0, index - 1)]
    threshold = max(
        boundary * ENVELOPE_POLICY.close_breach_percent / 100,
        prior_atr * ENVELOPE_POLICY.close_breach_atr,
    )
    return bars[index].close > boundary + threshold


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


def _state_rank(state: MajorLineState) -> int:
    return {
        MajorLineState.CRITICAL_BREAKOUT: 1,
        MajorLineState.BREAKOUT_RETEST: 3,
        MajorLineState.BROKEN_OUT: 2,
    }[state]
