"""Causal, reusable feature extraction for board-hotspot analysis."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from stock_harness.models import StoredDailyBar


BOARD_HOTSPOT_FEATURE_VERSION = "board-hotspot-features-v4-leading-context"


def extract_board_hotspot_features(
    bars: Sequence[StoredDailyBar],
    benchmark_bars: Sequence[StoredDailyBar],
    *,
    breadth_snapshot: Mapping[str, object] | None = None,
    member_snapshot: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build online-safe features using only bars at or before the cutoff."""
    ordered = sorted(bars, key=lambda bar: bar.trade_date)[-61:]
    benchmark = sorted(benchmark_bars, key=lambda bar: bar.trade_date)[-61:]
    breadth = dict(breadth_snapshot or {})
    members = dict(member_snapshot or {})
    returns = {
        str(period): _return(ordered, period)
        for period in (1, 3, 5, 10, 20, 40, 60)
    }
    benchmark_returns = {
        str(period): _return(benchmark, period)
        for period in (1, 3, 5, 10, 20, 40, 60)
    }
    relative = {
        period: _difference(returns[period], benchmark_returns[period])
        for period in returns
    }
    shape = _shape_features(ordered, relative)
    return {
        "feature_version": BOARD_HOTSPOT_FEATURE_VERSION,
        "coverage_state": "complete" if len(ordered) >= 21 else "insufficient",
        "metrics": {
            "returns": returns,
            "relative_strength": relative,
            "volume_ratio20": _last_volume_ratio(ordered, 20),
            "recent_volume_ratio_5_5": _window_volume_ratio(ordered, 5),
            "short_shape": {"state": _trend_state(ordered, 10, .02)},
            "medium_shape": {"state": _trend_state(ordered, 20, .04)},
            "long_shape": {"state": _trend_state(ordered, 60, .08)},
            "hotspot_shape": shape,
            "board_breadth": breadth,
        },
        "member_snapshot": members,
    }


def _return(bars: Sequence[StoredDailyBar], period: int) -> float | None:
    if len(bars) <= period or bars[-period - 1].close <= 0:
        return None
    return bars[-1].close / bars[-period - 1].close - 1


def _difference(left: object, right: object) -> float | None:
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return None
    return float(left) - float(right)


def _last_volume_ratio(
    bars: Sequence[StoredDailyBar], period: int,
) -> float | None:
    if len(bars) <= period:
        return None
    baseline = _average([bar.volume for bar in bars[-period - 1:-1]])
    return bars[-1].volume / baseline if baseline and baseline > 0 else None


def _window_volume_ratio(
    bars: Sequence[StoredDailyBar], period: int,
) -> float | None:
    if len(bars) < period * 2:
        return None
    recent = _average([bar.volume for bar in bars[-period:]])
    prior = _average([bar.volume for bar in bars[-period * 2:-period]])
    return recent / prior if recent is not None and prior and prior > 0 else None


def _average(values: Sequence[float | int]) -> float | None:
    return sum(values) / len(values) if values else None


def _trend_state(
    bars: Sequence[StoredDailyBar], period: int, threshold: float,
) -> str:
    if len(bars) < period:
        return "unavailable"
    window = bars[-period:]
    start = window[0].close
    if start <= 0:
        return "unavailable"
    change = window[-1].close / start - 1
    if change >= threshold:
        return "rising"
    if change <= -threshold:
        return "falling"
    return "sideways"


def _shape_features(
    bars: Sequence[StoredDailyBar], relative: Mapping[str, float | None],
) -> dict[str, object]:
    if len(bars) < 41:
        return {"path": "none", "reason": "insufficient-shape-history"}
    close = bars[-1].close
    ma10 = _average([bar.close for bar in bars[-10:]])
    ma20 = _average([bar.close for bar in bars[-20:]])
    slope10 = _normalized_slope([bar.close for bar in bars[-10:]])
    slope5 = _normalized_slope([bar.close for bar in bars[-5:]])
    slope20 = _normalized_slope([bar.close for bar in bars[-20:]])
    prior_slope20 = _normalized_slope([bar.close for bar in bars[-40:-20]])
    atr14 = _atr(bars[-15:])
    prior_high20 = max(bar.high for bar in bars[-21:-1])
    prior_high40 = max(bar.high for bar in bars[-41:-1])
    breakout20_atr = (
        (close - prior_high20) / atr14 if atr14 and atr14 > 0 else None
    )
    breakout40_atr = (
        (close - prior_high40) / atr14 if atr14 and atr14 > 0 else None
    )
    extension_from_ma20_atr = (
        (close - ma20) / atr14
        if ma20 is not None and atr14 and atr14 > 0 else None
    )
    recent_ranges = _true_ranges(bars[-6:])
    baseline_ranges = _true_ranges(bars[-21:])
    recent_range = _average(recent_ranges)
    baseline_range = _average(baseline_ranges)
    range_compression_5_20 = (
        recent_range / baseline_range
        if recent_range is not None and baseline_range and baseline_range > 0
        else None
    )
    position60 = _range_position(bars[-60:]) if len(bars) >= 60 else None
    higher_low = min(bar.low for bar in bars[-5:]) > min(
        bar.low for bar in bars[-10:-5]
    )
    return5 = _return(bars, 5)
    return20 = _return(bars, 20)
    rs5 = relative.get("5")
    rs20 = relative.get("20")
    above_ma20 = ma20 is not None and close > ma20
    ma_alignment = ma10 is not None and ma20 is not None and close > ma10 > ma20
    breakout20 = breakout20_atr is not None and breakout20_atr >= .10
    breakout40 = breakout40_atr is not None and breakout40_atr >= .10

    path = "none"
    if (
        breakout20 and higher_low and ma_alignment
        and return20 is not None and return20 >= -.06
        and rs5 is not None and rs5 >= .03
        and slope10 is not None and slope10 >= .003
        and prior_slope20 is not None and slope20 is not None
        and prior_slope20 <= 0
        and slope20 > prior_slope20
    ):
        path = "downtrend-reversal"
    elif (
        breakout20 and return5 is not None and return5 >= .025
        and rs5 is not None and rs5 >= .02 and slope5 is not None and slope5 > 0
    ):
        path = "platform-breakout"
    elif (
        return20 is not None and return20 >= .03
        and rs20 is not None and rs20 >= .025
        and slope20 is not None and slope20 > 0
        and above_ma20
    ):
        path = "trend-continuation"
    return {
        "path": path,
        "slope10": slope10,
        "slope5": slope5,
        "slope20": slope20,
        "prior_slope20": prior_slope20,
        "above_ma20": above_ma20,
        "ma_alignment": ma_alignment,
        "higher_low": higher_low,
        "breakout20": breakout20,
        "breakout40": breakout40,
        "breakout20_atr": breakout20_atr,
        "breakout40_atr": breakout40_atr,
        "range_compression_5_20": range_compression_5_20,
        "extension_from_ma20_atr": extension_from_ma20_atr,
        "position60": position60,
    }


def _range_position(bars: Sequence[StoredDailyBar]) -> float | None:
    if not bars:
        return None
    low = min(bar.low for bar in bars)
    high = max(bar.high for bar in bars)
    if high <= low:
        return None
    return (bars[-1].close - low) / (high - low)


def _normalized_slope(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    if mean <= 0:
        return None
    center = (len(values) - 1) / 2
    denominator = sum((index - center) ** 2 for index in range(len(values)))
    if denominator <= 0:
        return None
    slope = sum((index - center) * (value - mean)
                for index, value in enumerate(values)) / denominator
    return slope / mean


def _atr(bars: Sequence[StoredDailyBar]) -> float | None:
    if len(bars) < 2:
        return None
    ranges = []
    for previous, current in zip(bars, bars[1:]):
        ranges.append(max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        ))
    return sum(ranges) / len(ranges) if ranges else None


def _true_ranges(bars: Sequence[StoredDailyBar]) -> list[float]:
    return [
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in zip(bars, bars[1:])
    ]


def hotspot_feature_value(
    feature: Mapping[str, Any], path: tuple[str, ...],
) -> float | None:
    """Read a numeric feature path without leaking mapping details to evaluators."""
    value: object = feature
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return float(value) if isinstance(value, (int, float)) else None
