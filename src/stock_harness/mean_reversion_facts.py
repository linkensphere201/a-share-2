"""Causal daily facts shared by mean-reversion analysis systems."""

from __future__ import annotations

from collections.abc import Sequence
from math import isfinite
from statistics import fmean, median

from stock_harness.models import StoredDailyBar


FACT_VERSION = "mean-reversion-facts-v3"
MINIMUM_BARS = 120


def build_mean_reversion_facts(
    bars: Sequence[StoredDailyBar], *, volume_semantics: str = "traded",
) -> dict[str, object]:
    """Build point-in-time facts without assigning a system score or verdict."""
    ordered = sorted(bars, key=lambda bar: bar.trade_date)
    if len(ordered) < MINIMUM_BARS or any(
        bar.close <= 0 or not isfinite(bar.close) for bar in ordered
    ):
        return {
            "version": FACT_VERSION, "coverage_state": "insufficient",
            "disqualifiers": ["insufficient-history"],
        }
    closes = [float(bar.close) for bar in ordered]
    highs = [float(bar.high) for bar in ordered]
    lows = [float(bar.low) for bar in ordered]
    volumes = [max(0.0, float(bar.volume)) for bar in ordered]
    ema20 = _ema_series(closes, 20)
    ema60 = _ema_series(closes, 60)
    ema120 = _ema_series(closes, 120)
    atr = _atr_series(ordered, 14)
    current_atr = atr[-1]
    current = closes[-1]
    atr_percent = current_atr / current if current > 0 else 0.0
    center = ema20[-1]
    deviation = (current - center) / current_atr if current_atr > 0 else None
    recent_low_deviations = [
        (low - daily_center) / daily_atr
        for low, daily_center, daily_atr in zip(lows[-10:], ema20[-10:], atr[-10:])
        if daily_atr > 0
    ]
    recent_low_deviation = (
        min(recent_low_deviations) if recent_low_deviations else None
    )
    ema20_slope_atr = _slope_atr(ema20, current_atr, 10)
    ema60_slope_atr = _slope_atr(ema60, current_atr, 20)
    return20 = current / closes[-21] - 1
    return60 = current / closes[-61] - 1
    parent_uptrend = bool(
        ema20[-1] >= ema60[-1] * .985
        and ema60[-1] >= ema120[-1] * .98
        and ema60_slope_atr >= 0
        and current >= ema120[-1] * .98
    )
    persistent_decline_regime = bool(
        ema20[-1] < ema60[-1] < ema120[-1]
        and ema20_slope_atr < -.8 and ema60_slope_atr < -.5
        and return20 <= -.08 and return60 <= -.12
    )
    center_stable = bool(
        current_atr > 0 and ema20_slope_atr > -1.8 and ema60_slope_atr > -1.2
    )
    ranges = [
        max(bar.high - bar.low, abs(bar.high - ordered[index - 1].close),
            abs(bar.low - ordered[index - 1].close))
        for index, bar in enumerate(ordered) if index > 0
    ]
    recent_range_ratio = _ratio(
        median(ranges[-3:]), median(ranges[-13:-3]), default=1.0,
    )
    median_volume20 = median(volumes[-21:-1]) if volume_semantics == "traded" else 0
    volume_ratio = (
        volumes[-1] / median_volume20 if median_volume20 > 0 else None
    )
    down_volume_recent = [
        volumes[index] for index in range(len(ordered) - 5, len(ordered))
        if closes[index] < closes[index - 1]
    ]
    down_volume_baseline = [
        volumes[index] for index in range(len(ordered) - 20, len(ordered) - 5)
        if closes[index] < closes[index - 1]
    ]
    selling_volume_ratio = (
        _ratio(fmean(down_volume_recent), median(down_volume_baseline), default=1.0)
        if down_volume_recent and down_volume_baseline and volume_semantics == "traded"
        else None
    )
    speed_recent = _normalized_speed(closes, 3, atr_percent)
    speed_prior = _normalized_prior_speed(closes, 3, atr_percent)
    negative_momentum_decelerating = bool(
        speed_prior < -.05 and speed_recent > speed_prior * .8
    )
    prior_low = min(lows[-11:-3])
    recent_low = min(lows[-3:])
    low_extension_atr = (
        (prior_low - recent_low) / current_atr if current_atr > 0 else 0.0
    )
    latest_range = max(highs[-1] - lows[-1], 1e-9)
    close_location = (current - lows[-1]) / latest_range
    capitulation_absorbed = _capitulation_absorbed(
        ordered, volumes, median_volume20,
    ) if volume_semantics == "traded" else False
    exhaustion_signals = {
        "negative_momentum_decelerating": negative_momentum_decelerating,
        "range_contracting": recent_range_ratio <= .85,
        "new_low_progress_small": low_extension_atr <= .35,
        "close_location_improving": close_location >= .55,
        "selling_volume_contracting": (
            selling_volume_ratio is not None and selling_volume_ratio <= .9
        ),
        "capitulation_absorbed": capitulation_absorbed,
    }
    exhaustion_count = sum(exhaustion_signals.values())
    confirmation_boundary = max(highs[-5:-1])
    confirmed = bool(
        current > confirmation_boundary * 1.002
        and closes[-2] <= confirmation_boundary
        and close_location >= .6
    )
    confirmation_quality = _confirmation_quality(
        confirmed=confirmed,
        close_location=close_location,
        volume_ratio=volume_ratio,
        exhaustion_count=exhaustion_count,
        recent_range_ratio=recent_range_ratio,
        breakout_atr=(current - confirmation_boundary) / current_atr,
    )
    expanding_volume_decline = bool(
        closes[-1] < closes[-2]
        and volume_ratio is not None and volume_ratio >= 1.35
        and close_location <= .35
    )
    weak_rebound = bool(
        confirmed and volume_ratio is not None and volume_ratio < .7
    )
    volume_path = (
        "volume-backed-structural-break" if expanding_volume_decline and current < ema120[-1]
        else "expanding-volume-decline" if expanding_volume_decline
        else "capitulation-absorption" if capitulation_absorbed
        else "volume-backed-reclaim" if confirmed and volume_ratio is not None and volume_ratio >= 1.0
        else "shrinking-volume-rebound" if weak_rebound
        else "shrinking-volume-stabilization" if selling_volume_ratio is not None and selling_volume_ratio <= .9
        else "neutral"
    )
    structural_break = bool(
        current < ema120[-1] * .98 and ema20_slope_atr < -1.8
        and ema60_slope_atr < -1.2
    ) or volume_path == "volume-backed-structural-break"
    directional_candidate = bool(
        parent_uptrend and center_stable
        and recent_low_deviation is not None and recent_low_deviation <= -.65
    )
    oversold_candidate = bool(
        center_stable and recent_low_deviation is not None
        and recent_low_deviation <= -1.5 and exhaustion_count >= 3
    )
    setup_family = (
        "directional-pullback" if directional_candidate
        else "oversold-exhaustion" if oversold_candidate else "none"
    )
    if structural_break:
        state = "structural-break"
    elif setup_family == "none":
        state = "stable-center" if center_stable else "unqualified"
    elif confirmed and exhaustion_count >= 3 and not weak_rebound:
        state = "reversal-confirmed"
    elif exhaustion_count >= 3:
        state = "exhaustion-watch"
    elif recent_low_deviation is not None and recent_low_deviation <= -1.5:
        state = "extreme-pending"
    else:
        state = "deviation-building"
    holding_sessions = 20 if setup_family == "directional-pullback" else 10
    invalidation_lookback = 10 if holding_sessions == 20 else 5
    invalidation = min(lows[-invalidation_lookback:]) - current_atr * .25
    targets = _targets(
        current, invalidation, current_atr,
        [
            (center, "ema20-center", "mean-reversion", min(10, holding_sessions)),
            (_volume_weighted_center(ordered[-20:]), "turnover-weighted-center-20", "mean-reversion", holding_sessions),
            (max(highs[-21:-1]), "prior-20-session-high", "mean-reversion", holding_sessions),
            (max(highs[-61:-1]), "prior-60-session-high", "extension", 60),
        ],
    )
    chart = (
        {
            "center_points": _points(ordered[-120:], ema20[-120:]),
            "upper_band_points": _points_with_band(
                ordered[-120:], ema20[-120:], atr[-120:], 1.5,
            ),
            "lower_band_points": _points_with_band(
                ordered[-120:], ema20[-120:], atr[-120:], -1.5,
            ),
        }
        if setup_family != "none" else {}
    )
    return {
        "version": FACT_VERSION,
        "coverage_state": "complete",
        "as_of_date": ordered[-1].trade_date.isoformat(),
        "center": {
            "kind": "ema20", "price": _round(center),
            "ema20": _round(ema20[-1]), "ema60": _round(ema60[-1]),
            "ema120": _round(ema120[-1]),
            "ema20_slope_atr_10": _round(ema20_slope_atr),
            "ema60_slope_atr_20": _round(ema60_slope_atr),
            "stable": center_stable,
        },
        "deviation_atr": _round(deviation),
        "recent_low_deviation_atr": _round(recent_low_deviation),
        "atr14": _round(current_atr), "atr_percent": _round(atr_percent),
        "parent_trend": "up" if parent_uptrend else "not-up",
        "regime": {
            "classification": (
                "up" if parent_uptrend else "persistent-decline-risk"
                if persistent_decline_regime else "mixed"
            ),
            "return20": _round(return20), "return60": _round(return60),
            "persistent_one_way_decline": persistent_decline_regime,
            "causal_through": ordered[-1].trade_date.isoformat(),
        },
        "momentum": {
            "speed_recent": _round(speed_recent),
            "speed_prior": _round(speed_prior),
            "negative_decelerating": negative_momentum_decelerating,
        },
        "exhaustion": {
            "signal_count": exhaustion_count,
            "signals": exhaustion_signals,
            "range_ratio_3_to_prior10": _round(recent_range_ratio),
            "low_extension_atr": _round(low_extension_atr),
            "close_location": _round(close_location),
        },
        "price_volume": {
            "path": volume_path, "volume_semantics": volume_semantics,
            "volume_ratio20": _round(volume_ratio),
            "selling_volume_ratio": _round(selling_volume_ratio),
        },
        "setup_family": setup_family, "state": state,
        "confirmation": {
            "confirmed": confirmed, "boundary_price": _round(confirmation_boundary),
            "entry_price": _round(current) if confirmed else None,
            "quality_score": _round(confirmation_quality),
            "breakout_atr": _round((current - confirmation_boundary) / current_atr),
        },
        "invalidation_price": _round(invalidation),
        "invalidation": {
            "price": _round(invalidation),
            "basis": f"local-{invalidation_lookback}-session-low-minus-0.25-atr",
            "lookback_sessions": invalidation_lookback,
        },
        "maximum_holding_sessions": holding_sessions,
        "targets": targets,
        "structural_break": structural_break,
        "chart": chart,
        "disqualifiers": [
            *([] if center_stable else ["unstable-center"]),
            *(["structural-break"] if structural_break else []),
            *(["expanding-volume-decline"] if expanding_volume_decline else []),
            *(["weak-shrinking-volume-rebound"] if weak_rebound else []),
            *(["persistent-decline-regime"] if persistent_decline_regime else []),
        ],
    }


def _ema_series(values: Sequence[float], period: int) -> list[float]:
    alpha = 2.0 / (period + 1)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(alpha * float(value) + (1 - alpha) * result[-1])
    return result


def _atr_series(bars: Sequence[StoredDailyBar], period: int) -> list[float]:
    true_ranges = [float(bars[0].high - bars[0].low)]
    for prior, current in zip(bars, bars[1:]):
        true_ranges.append(max(
            float(current.high - current.low),
            abs(float(current.high - prior.close)),
            abs(float(current.low - prior.close)),
        ))
    result = [true_ranges[0]]
    for value in true_ranges[1:]:
        result.append((result[-1] * (period - 1) + value) / period)
    return result


def _slope_atr(values: Sequence[float], atr: float, lookback: int) -> float:
    return (values[-1] - values[-lookback - 1]) / atr if atr > 0 else 0.0


def _normalized_speed(values: Sequence[float], period: int, atr_percent: float) -> float:
    if len(values) <= period or values[-period - 1] <= 0 or atr_percent <= 0:
        return 0.0
    return (values[-1] / values[-period - 1] - 1) / atr_percent / period


def _normalized_prior_speed(values: Sequence[float], period: int, atr_percent: float) -> float:
    if len(values) <= period * 2 or values[-period * 2 - 1] <= 0 or atr_percent <= 0:
        return 0.0
    return (values[-period - 1] / values[-period * 2 - 1] - 1) / atr_percent / period


def _capitulation_absorbed(
    bars: Sequence[StoredDailyBar], volumes: Sequence[float], baseline: float,
) -> bool:
    if baseline <= 0:
        return False
    for index in range(len(bars) - 5, len(bars) - 1):
        if (
            bars[index].close < bars[index - 1].close
            and volumes[index] >= baseline * 1.8
            and min(bar.close for bar in bars[index + 1:]) > bars[index].low
        ):
            return True
    return False


def _volume_weighted_center(bars: Sequence[StoredDailyBar]) -> float:
    denominator = sum(max(0.0, float(bar.volume)) for bar in bars)
    if denominator <= 0:
        return fmean(float(bar.close) for bar in bars)
    return sum(
        ((float(bar.high) + float(bar.low) + float(bar.close)) / 3)
        * max(0.0, float(bar.volume)) for bar in bars
    ) / denominator


def _targets(
    entry: float, invalidation: float, atr: float,
    candidates: Sequence[tuple[float, str, str, int]],
) -> list[dict[str, object]]:
    risk = entry - invalidation
    if risk <= 0:
        return []
    result = []
    seen: list[float] = []
    for price, basis, target_class, horizon in sorted(candidates, key=lambda item: item[0]):
        if price <= entry + atr * .2 or any(abs(price - value) <= atr * .25 for value in seen):
            continue
        raw = (price - entry) / risk
        stressed = max(0.0, (price - entry) * .9 / (risk * 1.1))
        result.append({
            "label": f"T{len(result) + 1}", "price": _round(price),
            "basis": basis, "risk_reward_ratio": _round(raw),
            "stressed_risk_reward_ratio": _round(stressed),
            "target_class": target_class,
            "maximum_holding_sessions": horizon,
        })
        seen.append(price)
    return result


def _confirmation_quality(
    *, confirmed: bool, close_location: float, volume_ratio: float | None,
    exhaustion_count: int, recent_range_ratio: float, breakout_atr: float,
) -> float:
    if not confirmed:
        return 0.0
    location_points = min(25.0, max(0.0, (close_location - .5) / .5 * 25.0))
    volume_points = (
        20.0 if volume_ratio is not None and volume_ratio >= 1.0
        else 10.0 if volume_ratio is None or volume_ratio >= .7 else 0.0
    )
    exhaustion_points = min(20.0, exhaustion_count * 4.0)
    contraction_points = min(15.0, max(0.0, (1.2 - recent_range_ratio) / .6 * 15.0))
    breakout_points = min(20.0, max(8.0, breakout_atr * 40.0))
    return min(100.0, location_points + volume_points + exhaustion_points
               + contraction_points + breakout_points)


def _points(bars: Sequence[StoredDailyBar], values: Sequence[float]) -> list[dict[str, object]]:
    return [
        {"date": bar.trade_date.isoformat(), "price": _round(value)}
        for bar, value in zip(bars, values)
    ]


def _points_with_band(
    bars: Sequence[StoredDailyBar], centers: Sequence[float], atrs: Sequence[float],
    multiplier: float,
) -> list[dict[str, object]]:
    return [
        {"date": bar.trade_date.isoformat(), "price": _round(center + multiplier * atr)}
        for bar, center, atr in zip(bars, centers, atrs)
    ]


def _ratio(left: float, right: float, *, default: float) -> float:
    return left / right if right > 0 else default


def _round(value: float | None) -> float | None:
    return round(value, 6) if value is not None and isfinite(value) else None
