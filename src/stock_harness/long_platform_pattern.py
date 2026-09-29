"""Causal ongoing long platforms shared by charts and screening."""

from dataclasses import asdict, dataclass
from math import isfinite
from statistics import fmean, median

from stock_harness.analysis_inputs import AnalysisBar

STRATEGY_ID = "long-consolidation-platform"
ALGORITHM_VERSION = "long-consolidation-platform-v2"
KIND = "long-platform-range"


@dataclass(frozen=True, slots=True)
class LongPlatformConfig:
    minimum_sessions: int = 60
    maximum_sessions: int = 250
    recent_sessions: int = 20
    maximum_range: float = .20
    maximum_recent_range: float = .08
    maximum_range_ratio: float = .75
    maximum_center_drift: float = .06
    maximum_fitted_drift: float = .08
    maximum_volume_ratio: float = .95
    directional_return: float = .03
    maximum_directional_efficiency: float = .65
    minimum_boundary_touches: int = 0


CONFIG = LongPlatformConfig()


def _slope(values):
    center = (len(values) - 1) / 2
    return sum((i - center) * value for i, value in enumerate(values)) / sum(
        (i - center) ** 2 for i in range(len(values)))


def detect_long_platform(bars: tuple[AnalysisBar, ...] | list[AnalysisBar], *,
                         config: LongPlatformConfig = CONFIG) -> dict[str, object] | None:
    bars = tuple(bars[-config.maximum_sessions - 60:])
    if len(bars) < config.minimum_sessions:
        return None
    if any(not b.period_complete or b.contains_provisional or b.contains_roll_event
           or not all(isfinite(v) and v > 0 for v in (b.open, b.high, b.low, b.close, b.volume))
           or not b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high for b in bars):
        return None
    if any(a.period_end >= b.period_start for a, b in zip(bars, bars[1:])):
        return None
    recent = bars[-config.recent_sessions:]
    recent_range = max(b.high for b in recent) / min(b.low for b in recent) - 1
    if recent_range > config.maximum_recent_range:
        return None
    # Longest qualifying interval owns the geometry; no fixed calendar start.
    for size in range(min(len(bars), config.maximum_sessions), config.minimum_sessions - 1, -1):
        window = bars[-size:]
        history = window[:-config.recent_sessions]
        upper, lower = max(b.high for b in history), min(b.low for b in history)
        width = upper / lower - 1
        if not .01 <= width <= config.maximum_range:
            continue
        if config.minimum_boundary_touches:
            tolerance = (upper - lower) * .10
            upper_touches = [i for i, b in enumerate(window) if b.high >= upper - tolerance]
            lower_touches = [i for i, b in enumerate(window) if b.low <= lower + tolerance]
            if any(len(touches) < config.minimum_boundary_touches or touches[-1] - touches[0] < 3
                   for touches in (upper_touches, lower_touches)):
                continue
        if any(b.high > upper or b.low < lower for b in recent):
            continue
        if recent_range > width * config.maximum_range_ratio:
            continue
        closes = [b.close for b in window]
        center_drift = fmean(closes[-20:]) / fmean(closes[:20]) - 1
        fitted_drift = _slope(closes) * (size - 1) / fmean(closes)
        path = sum(abs(b - a) for a, b in zip(closes, closes[1:]))
        efficiency = abs(closes[-1] - closes[0]) / path if path else 0.
        change = closes[-1] / closes[0] - 1
        if (abs(center_drift) > config.maximum_center_drift
                or abs(fitted_drift) > config.maximum_fitted_drift
                or (abs(change) > config.directional_return and efficiency > config.maximum_directional_efficiency)):
            continue
        volume_ratio = fmean(b.volume for b in recent) / fmean(b.volume for b in history)
        # A single historical spike must not manufacture apparent contraction.
        median_volume_ratio = median(b.volume for b in recent) / median(b.volume for b in history)
        if max(volume_ratio, median_volume_ratio) > config.maximum_volume_ratio:
            continue
        before = bars[:len(bars) - size][-60:]
        prior_change = before[-1].close / before[0].close - 1 if len(before) >= 20 else None
        platform_type = ("low-base" if prior_change is not None and prior_change <= -.15 else
                         "continuation" if prior_change is not None and prior_change >= .15 else "neutral")
        up = [b.volume for b in recent if b.close > b.open]
        down = [b.volume for b in recent if b.close < b.open]
        demand = fmean(up) / fmean(down) if up and down else None
        position = (closes[-1] - lower) / (upper - lower)
        floor_lift = min(b.low for b in recent) / lower - 1
        ma_slopes = {}
        for period in (60, 240):
            ma_slopes[f"ma{period}_slope_percent"] = (
                round((fmean(b.close for b in bars[-period:]) /
                       fmean(b.close for b in bars[-period-10:-10]) - 1) * 100, 4)
                if len(bars) >= period + 10 else None)
        score = (30 + 20 * (1 - recent_range / width) + 15 * (1 - min(1., volume_ratio))
                 + 15 * (1 - min(1., abs(center_drift) / config.maximum_center_drift))
                 + 10 * min(1., size / 250) + (10 * min(1., max(0., demand - 1)) if demand is not None else 0))
        return {
            "kind": KIND, "algorithm_version": ALGORITHM_VERSION, "display_name": "Long consolidation platform",
            "stage": "shape-match", "screen_eligible": True,
            "platform_stage": "near-upper" if position >= .8 else "tightening",
            "platform_type": platform_type, "platform_sessions": size,
            "start_date": window[0].period_start.isoformat(), "end_date": bars[-1].period_end.isoformat(),
            "as_of_date": bars[-1].period_end.isoformat(), "lower": lower, "upper": upper,
            "center": (upper + lower) / 2, "latest_close": closes[-1], "score": round(score, 4),
            "platform_range_percent": round(width * 100, 4), "recent_range_percent": round(recent_range * 100, 4),
            "center_drift_percent": round(center_drift * 100, 4), "fitted_drift_percent": round(fitted_drift * 100, 4),
            "recent_history_volume_ratio": round(volume_ratio, 4),
            "recent_history_median_volume_ratio": round(median_volume_ratio, 4),
            "up_down_volume_ratio": round(demand, 4) if demand is not None else None,
            "floor_lift_percent": round(floor_lift * 100, 4), "range_position": round(position, 4),
            "prior_return_percent": round(prior_change * 100, 4) if prior_change is not None else None,
            "context_sessions": len(before), "small_body_fraction": round(sum(
                abs(b.close - b.open) <= b.close * .015 for b in recent) / len(recent), 4),
            **ma_slopes, "parameters": asdict(config),
            "uncertainty": "Provisional price-volume structure, not institutional accumulation or a return forecast.",
        }
    return None
