"""Versioned broad-market liquidity capacity and direction classification."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from stock_harness.models import StoredDailyBar


MARKET_LIQUIDITY_VERSION = "market-liquidity-capacity-v1"


@dataclass(frozen=True)
class MarketLiquidityThresholds:
    low_capacity_cny: float = 2.5e12
    high_capacity_cny: float = 3.2e12
    contraction_ratio: float = .94
    expansion_ratio: float = 1.06
    slope_threshold: float = .005


def analyze_market_liquidity(
    turnover: Sequence[float], *, source: str = "all-stock-close-volume",
    thresholds: MarketLiquidityThresholds = MarketLiquidityThresholds(),
) -> dict[str, object]:
    """Combine absolute capacity with marginal direction using causal inputs."""
    values = [float(value) for value in turnover[-25:] if value > 0]
    recent = _median(values[-5:]) if len(values) >= 5 else None
    baseline = _median(values[-25:-5]) if len(values) >= 25 else None
    ratio = recent / baseline if recent is not None and baseline else None
    slope = _theil_sen_normalized_slope(values[-10:])
    falling_days = sum(right < left for left, right in zip(values[-6:-1], values[-5:]))
    rising_days = sum(right > left for left, right in zip(values[-6:-1], values[-5:]))

    if recent is None:
        capacity = "unknown"
    elif recent < thresholds.low_capacity_cny:
        capacity = "low"
    elif recent >= thresholds.high_capacity_cny:
        capacity = "high"
    else:
        capacity = "medium"
    if ratio is not None and ratio < thresholds.contraction_ratio \
            and slope is not None and slope < -thresholds.slope_threshold \
            and falling_days >= 3:
        direction = "contracting"
    elif ratio is not None and ratio > thresholds.expansion_ratio \
            and slope is not None and slope > thresholds.slope_threshold \
            and rising_days >= 3:
        direction = "expanding"
    else:
        direction = "neutral"

    raw_seats = _seat_budget(capacity, direction)
    return {
        "version": MARKET_LIQUIDITY_VERSION,
        "capacity_tier": capacity,
        "direction": direction,
        "regime": f"{capacity}-{direction}",
        "raw_visible_seats": raw_seats,
        "absolute_turnover_5_median": recent,
        "baseline_turnover_20_median": baseline,
        "volume_ratio_5_20": ratio,
        "volume_slope_10": slope,
        "falling_days_5": falling_days,
        "rising_days_5": rising_days,
        "source": source,
    }


def analyze_benchmark_volume_fallback(
    bars: Sequence[StoredDailyBar],
) -> dict[str, object]:
    result = analyze_market_liquidity(
        [float(bar.volume) for bar in sorted(bars, key=lambda bar: bar.trade_date)],
        source="benchmark-volume-fallback",
        thresholds=MarketLiquidityThresholds(low_capacity_cny=0, high_capacity_cny=0),
    )
    result["capacity_tier"] = "unknown"
    result["regime"] = f"unknown-{result['direction']}"
    result["raw_visible_seats"] = 1
    return result


def stabilize_seat_budget(
    current: Mapping[str, object], prior: Mapping[str, object],
) -> tuple[int, int]:
    """Reduce capacity immediately; require two sessions before expanding seats."""
    raw = int(current.get("raw_visible_seats") or 1)
    prior_raw = int(prior.get("market_liquidity_raw_seats") or raw)
    streak = int(prior.get("market_liquidity_seat_streak") or 0) + 1 \
        if prior_raw == raw else 1
    prior_seats = int(prior.get("radar_slot_limit") or raw)
    if raw < prior_seats:
        return raw, streak
    if raw > prior_seats and streak < 2:
        return prior_seats, streak
    return raw, streak


def _seat_budget(capacity: str, direction: str) -> int:
    if capacity in {"low", "unknown"}:
        return 1
    if capacity == "medium":
        return {"contracting": 1, "neutral": 2, "expanding": 3}[direction]
    return {"contracting": 2, "neutral": 3, "expanding": 5}[direction]


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _theil_sen_normalized_slope(values: Sequence[float]) -> float | None:
    baseline = _median(values)
    if len(values) < 2 or baseline is None or baseline <= 0:
        return None
    slopes = [
        (values[right] - values[left]) / (right - left)
        for left in range(len(values) - 1)
        for right in range(left + 1, len(values))
    ]
    slope = _median(slopes)
    return slope / baseline if slope is not None else None
