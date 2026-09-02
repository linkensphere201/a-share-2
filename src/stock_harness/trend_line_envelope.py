"""Shared causal price-envelope integrity for generated trend lines."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar


class EnvelopeSide(StrEnum):
    UPPER = "upper"
    LOWER = "lower"


@dataclass(frozen=True, slots=True)
class EnvelopePolicy:
    wick_breach_percent: float = 1.0
    wick_breach_atr: float = 0.5
    close_breach_percent: float = 0.5
    close_breach_atr: float = 0.35
    contact_percent: float = 2.0
    contact_atr: float = 0.75


@dataclass(frozen=True, slots=True)
class EnvelopeIntegrity:
    touch_count: int
    independent_touch_count: int
    wick_breach_count: int
    body_breach_count: int
    close_breach_count: int
    dominant_extreme_count: int
    maximum_wick_breach_percent: float
    maximum_body_breach_percent: float
    maximum_close_breach_percent: float
    maximum_breach_atr: float


def evaluate_trend_line_envelope(
    bars: Sequence[AnalysisBar],
    atrs: Sequence[float],
    first_index: int,
    second_index: int,
    first_price: float,
    slope_per_bar: float,
    evaluation_end_index: int,
    contact_indexes: Sequence[int],
    independent_separation_bars: int,
    side: EnvelopeSide,
    policy: EnvelopePolicy = EnvelopePolicy(),
) -> EnvelopeIntegrity:
    """Evaluate every pre-event bar without allowing a bar to inflate its own ATR."""
    wick_breaches = body_breaches = close_breaches = dominant_extremes = 0
    maximum_wick_percent = maximum_body_percent = 0.0
    maximum_close_percent = maximum_atr = 0.0
    for index in range(first_index + 1, evaluation_end_index + 1):
        boundary = first_price + slope_per_bar * (index - first_index)
        if boundary <= 0:
            wick_breaches += 1
            continue
        atr = max(atrs[max(0, index - 1)], boundary * 0.001, 1e-9)
        wick_excess = _directed_excess(
            bars[index].high if side is EnvelopeSide.UPPER else bars[index].low,
            boundary,
            side,
        )
        close_excess = _directed_excess(bars[index].close, boundary, side)
        body_edge = (
            max(bars[index].open, bars[index].close)
            if side is EnvelopeSide.UPPER
            else min(bars[index].open, bars[index].close)
        )
        body_excess = _directed_excess(body_edge, boundary, side)
        maximum_wick_percent = max(maximum_wick_percent, wick_excess / boundary * 100)
        maximum_body_percent = max(maximum_body_percent, body_excess / boundary * 100)
        maximum_close_percent = max(maximum_close_percent, close_excess / boundary * 100)
        maximum_atr = max(
            maximum_atr, wick_excess / atr, body_excess / atr, close_excess / atr
        )
        if wick_excess > max(
            boundary * policy.wick_breach_percent / 100,
            atr * policy.wick_breach_atr,
        ):
            wick_breaches += 1
        if body_excess > max(
            boundary * policy.close_breach_percent / 100,
            atr * policy.close_breach_atr,
        ):
            body_breaches += 1
        if close_excess > max(
            boundary * policy.close_breach_percent / 100,
            atr * policy.close_breach_atr,
        ):
            close_breaches += 1
        extreme = bars[index].high if side is EnvelopeSide.UPPER else bars[index].low
        if _directed_excess(extreme, first_price, side) > max(
            first_price * policy.wick_breach_percent / 100,
            atr * policy.wick_breach_atr,
        ):
            dominant_extremes += 1

    contacts = []
    for index in contact_indexes:
        if not first_index <= index <= evaluation_end_index:
            continue
        boundary = first_price + slope_per_bar * (index - first_index)
        atr = max(atrs[max(0, index - 1)], boundary * 0.001, 1e-9)
        observed = bars[index].high if side is EnvelopeSide.UPPER else bars[index].low
        if abs(observed - boundary) <= max(
            boundary * policy.contact_percent / 100,
            atr * policy.contact_atr,
        ):
            contacts.append(index)
    independent = [
        index for index in contacts
        if abs(index - first_index) > independent_separation_bars
        and abs(index - second_index) > independent_separation_bars
    ]
    return EnvelopeIntegrity(
        touch_count=len(contacts),
        independent_touch_count=len(independent),
        wick_breach_count=wick_breaches,
        body_breach_count=body_breaches,
        close_breach_count=close_breaches,
        dominant_extreme_count=dominant_extremes,
        maximum_wick_breach_percent=round(maximum_wick_percent, 6),
        maximum_body_breach_percent=round(maximum_body_percent, 6),
        maximum_close_breach_percent=round(maximum_close_percent, 6),
        maximum_breach_atr=round(maximum_atr, 6),
    )


def dominates_prior_extremes(
    bars: Sequence[AnalysisBar],
    index: int,
    lookback: int,
    atr: float,
    side: EnvelopeSide,
    tolerance_percent: float = 1.0,
    tolerance_atr: float = 0.5,
) -> bool:
    prior = bars[max(0, index - lookback):index]
    if not prior:
        return True
    price = bars[index].high if side is EnvelopeSide.UPPER else bars[index].low
    tolerance = max(abs(price) * tolerance_percent / 100, atr * tolerance_atr)
    if side is EnvelopeSide.UPPER:
        return price + tolerance >= max(item.high for item in prior)
    return price - tolerance <= min(item.low for item in prior)


def _directed_excess(observed: float, boundary: float, side: EnvelopeSide) -> float:
    return max(0.0, observed - boundary) if side is EnvelopeSide.UPPER else max(0.0, boundary - observed)
