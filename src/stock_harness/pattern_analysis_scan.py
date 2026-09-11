"""Bounded first-pass structure features owned by pattern analysis."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from statistics import fmean

from stock_harness.models import StoredDailyBar


@dataclass(frozen=True, slots=True)
class DailyStructureScan:
    atr5: float
    atr14: float
    atr20: float
    short_shape: Mapping[str, object]
    medium_shape: Mapping[str, object]
    descending_envelopes: Mapping[str, Mapping[str, object] | None]
    downside_deceleration: Mapping[str, object]


def scan_daily_structure(
    bars: Sequence[StoredDailyBar],
    *,
    short_period: int = 14,
    medium_period: int = 28,
) -> DailyStructureScan:
    if len(bars) < max(20, medium_period):
        raise ValueError("daily structure scan requires medium-horizon history")
    closes = [bar.close for bar in bars]
    atr5 = _atr(bars, 5)
    atr14 = _atr(bars, 14)
    atr20 = _atr(bars, 20)
    return DailyStructureScan(
        atr5=atr5,
        atr14=atr14,
        atr20=atr20,
        short_shape=_shape(closes, short_period),
        medium_shape=_shape(closes, medium_period),
        descending_envelopes={
            label: _descending_upper_envelope(bars, period, atr14)
            for label, period in (("3m", 63), ("6m", 126), ("1y", 250))
        },
        downside_deceleration=_downside_deceleration(bars, atr14),
    )


def _atr(bars: Sequence[StoredDailyBar], periods: int) -> float:
    selected = bars[-(periods + 1):]
    ranges = [
        max(current.high - current.low, abs(current.high - previous.close),
            abs(current.low - previous.close))
        for previous, current in zip(selected, selected[1:])
    ]
    return fmean(ranges) if ranges else 0.0


def _shape(closes: Sequence[float], periods: int) -> dict[str, object]:
    values = list(closes[-periods:])
    if len(values) < periods:
        return {"state": "insufficient", "slope_per_10": None}
    logs = [math.log(value) for value in values if value > 0]
    if len(logs) != len(values):
        return {"state": "invalid", "slope_per_10": None}
    slope = _linear_slope(logs)
    change = math.exp(slope * 10) - 1
    state = "rising" if change >= .02 else "falling" if change <= -.02 else "sideways"
    return {"state": state, "slope_per_10": _round(change)}


def _descending_upper_envelope(
    bars: Sequence[StoredDailyBar], periods: int, atr14: float,
) -> dict[str, object] | None:
    if len(bars) < periods + 1 or atr14 <= 0:
        return None
    history = list(bars[-(periods + 1):-1])
    highs = [bar.high for bar in history]
    slope = _linear_slope(highs)
    if slope >= 0:
        return None
    intercept = fmean(highs) - slope * (len(highs) - 1) / 2
    intercept += max(value - (intercept + slope * index) for index, value in enumerate(highs))
    boundary = intercept + slope * len(highs)
    latest = bars[-1]
    distance = (boundary - latest.close) / atr14
    buffer = max(boundary * .005, atr14 * .25)
    state = (
        "broken" if latest.close > boundary + buffer
        else "approaching" if 0 <= distance <= 1
        else "none"
    )
    return {
        "period_bars": periods,
        "start_date": history[0].trade_date.isoformat(),
        "end_date": latest.trade_date.isoformat(),
        "start_price": _round(intercept),
        "end_price": _round(boundary),
        "boundary": _round(boundary),
        "slope_per_bar": _round(slope),
        "distance_atr": _round(distance),
        "confirmation_price": _round(boundary + buffer),
        "confirmation_buffer_atr": _round(buffer / atr14),
        "invalidation_price": _round(boundary - atr14 * .25),
        "invalidation_buffer_atr": .25,
        "state": state,
    }


def _downside_deceleration(
    bars: Sequence[StoredDailyBar], atr14: float,
) -> dict[str, object]:
    closes = [bar.close for bar in bars]
    latest = closes[-1]
    high20 = max(bar.high for bar in bars[-20:])
    ma20 = fmean(closes[-20:])
    extended = atr14 > 0 and (
        (high20 - latest) / atr14 >= 2.5 or (ma20 - latest) / atr14 >= 1.5
    )
    recent = closes[-6:]
    prior = closes[-11:-5]
    recent_negative = sum(
        abs(after / before - 1)
        for before, after in zip(recent, recent[1:]) if after < before
    )
    prior_negative = sum(
        abs(after / before - 1)
        for before, after in zip(prior, prior[1:]) if after < before
    )
    count = 0
    facts: list[str] = []
    if prior_negative > 0 and recent_negative <= prior_negative * .65:
        count += 1
        facts.append("negative-return-deceleration")
    if latest >= min(closes[-4:]):
        count += 1
        facts.append("no-new-low-three-sessions")
    recent_bodies = fmean(abs(bar.close - bar.open) for bar in bars[-3:])
    prior_bodies = fmean(abs(bar.close - bar.open) for bar in bars[-8:-3])
    if prior_bodies > 0 and recent_bodies <= prior_bodies * .8:
        count += 1
        facts.append("body-contraction")
    recent_down_volume = [
        bar.volume for before, bar in zip(bars[-6:-1], bars[-5:])
        if bar.close < before.close
    ]
    prior_down_volume = [
        bar.volume for before, bar in zip(bars[-11:-6], bars[-10:-5])
        if bar.close < before.close
    ]
    if (
        recent_down_volume and prior_down_volume
        and fmean(recent_down_volume) <= fmean(prior_down_volume) * .85
    ):
        count += 1
        facts.append("sell-volume-contraction")
    if _linear_slope([math.log(value) for value in closes[-14:]]) > _linear_slope(
        [math.log(value) for value in closes[-28:-14]]
    ):
        count += 1
        facts.append("slope-improvement")
    return {"extended": extended, "deceleration_count": count, "facts": facts}


def _linear_slope(values: Sequence[float]) -> float:
    count = len(values)
    center = (count - 1) / 2
    denominator = sum((index - center) ** 2 for index in range(count))
    if not denominator:
        return 0.0
    return sum(
        (index - center) * value for index, value in enumerate(values)
    ) / denominator


def _round(value: float | None) -> float | None:
    return round(value, 6) if value is not None and math.isfinite(value) else None
