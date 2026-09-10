"""Causal, reusable feature extraction for board-hotspot analysis."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from stock_harness.models import StoredDailyBar


BOARD_HOTSPOT_FEATURE_VERSION = "board-hotspot-features-v1"


def extract_board_hotspot_features(
    bars: Sequence[StoredDailyBar],
    benchmark_bars: Sequence[StoredDailyBar],
    *,
    breadth_snapshot: Mapping[str, object] | None = None,
    member_snapshot: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build online-safe features using only bars at or before the cutoff."""
    ordered = sorted(bars, key=lambda bar: bar.trade_date)
    benchmark = sorted(benchmark_bars, key=lambda bar: bar.trade_date)
    breadth = dict(breadth_snapshot or {})
    members = dict(member_snapshot or {})
    returns = {str(period): _return(ordered, period) for period in (5, 20)}
    benchmark_returns = {
        str(period): _return(benchmark, period) for period in (5, 20)
    }
    relative = {
        period: _difference(returns[period], benchmark_returns[period])
        for period in returns
    }
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


def _average(values: Sequence[int]) -> float | None:
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
