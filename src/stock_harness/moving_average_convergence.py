"""Causal moving-average convergence and directional release detection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from statistics import median
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.trend_lines_analysis import TrendHorizon


class MovingAverageConvergenceState(StrEnum):
    CONVERGING = "converging"
    COMPRESSED = "compressed"
    BULLISH_EXPANSION = "bullish-expansion"
    BEARISH_EXPANSION = "bearish-expansion"


@dataclass(frozen=True, slots=True)
class MovingAverageConvergenceConfig:
    periods: tuple[int, ...]
    maximum_spread_percent: float
    maximum_spread_atr: float = 1.25
    minimum_compressed_bars: int = 3
    contraction_lookback_bars: int = 8
    maximum_contraction_ratio: float = 0.85
    recent_release_bars: int = 3
    slope_lookback_bars: int = 3
    release_buffer_percent: float = 0.003
    volume_lookback_bars: int = 20

    def validate(self) -> None:
        if len(self.periods) < 3 or tuple(sorted(set(self.periods))) != self.periods:
            raise ValueError("moving-average periods must contain three or more increasing values")
        if not 0 < self.maximum_spread_percent <= 0.15:
            raise ValueError("maximum moving-average spread must be between zero and 15%")
        if not 0 < self.maximum_spread_atr <= 5:
            raise ValueError("maximum moving-average ATR spread must be between zero and five")
        if self.minimum_compressed_bars < 2:
            raise ValueError("moving-average convergence requires at least two compressed bars")
        if self.contraction_lookback_bars < 3 or self.recent_release_bars < 1:
            raise ValueError("moving-average convergence lookbacks are too short")


@dataclass(frozen=True, slots=True)
class MovingAverageConvergencePattern:
    state: MovingAverageConvergenceState
    display_name: str
    direction: str
    periods: tuple[int, ...]
    start_date: date
    end_date: date
    available_date: date
    event_date: date | None
    upper_start: float
    lower_start: float
    upper_end: float
    lower_end: float
    center_start: float
    center_end: float
    current_values: tuple[float, ...]
    band_points: tuple[tuple[date, float, float], ...]
    spread_percent: float
    spread_atr: float
    contraction_ratio: float
    compressed_bars: int
    volume_ratio: float | None
    invalidation_price: float
    score: float
    score_components: dict[str, float]


@dataclass(frozen=True, slots=True)
class _BandPoint:
    index: int
    values: tuple[float, ...]
    upper: float
    lower: float
    center: float
    spread_percent: float
    spread_atr: float
    compressed: bool


def convergence_config(
    horizon: TrendHorizon,
    volatility_ratio: float,
) -> MovingAverageConvergenceConfig:
    periods, lower, upper, volatility_weight, maximum_spread_atr, minimum_bars = {
        TrendHorizon.SHORT: ((5, 10, 20), 0.008, 0.015, 0.55, 0.70, 3),
        TrendHorizon.MEDIUM: ((5, 20, 60), 0.012, 0.022, 0.75, 0.90, 4),
        TrendHorizon.LONG: ((20, 60, 120), 0.018, 0.035, 1.00, 1.10, 5),
    }[horizon]
    threshold = max(lower, min(upper, volatility_ratio * volatility_weight))
    return MovingAverageConvergenceConfig(
        periods=periods,
        maximum_spread_percent=round(threshold, 6),
        maximum_spread_atr=maximum_spread_atr,
        minimum_compressed_bars=minimum_bars,
    )


def detect_moving_average_convergence(
    bars: Sequence[AnalysisBar],
    config: MovingAverageConvergenceConfig,
) -> MovingAverageConvergencePattern | None:
    config.validate()
    minimum_history = max(config.periods) + max(
        config.contraction_lookback_bars,
        config.slope_lookback_bars,
    )
    if len(bars) < minimum_history:
        return None
    points = _band_points(bars, config)
    latest = points[-1]
    run_end = latest.index if latest.compressed else _recent_compressed_end(
        points, latest.index, config.recent_release_bars
    )
    if run_end is None:
        return None
    run_start = run_end
    by_index = {point.index: point for point in points}
    while run_start - 1 in by_index and by_index[run_start - 1].compressed:
        run_start -= 1
    compressed_bars = run_end - run_start + 1
    if compressed_bars < config.minimum_compressed_bars:
        return None

    start = by_index[run_start]
    available_index = run_start + config.minimum_compressed_bars - 1
    contraction_ratio = _contraction_ratio(points, latest.index, config)
    state = (
        MovingAverageConvergenceState.CONVERGING
        if latest.compressed and contraction_ratio <= config.maximum_contraction_ratio
        else MovingAverageConvergenceState.COMPRESSED
    )
    direction = "neutral"
    event_date = None
    if not latest.compressed:
        release = _release_direction(points, bars, run_end, config)
        if release is None:
            return None
        direction, event_index = release
        state = (
            MovingAverageConvergenceState.BULLISH_EXPANSION
            if direction == "bullish"
            else MovingAverageConvergenceState.BEARISH_EXPANSION
        )
        event_date = bars[event_index].period_end

    volume_ratio = _volume_ratio(bars, config.volume_lookback_bars)
    compactness = max(0.0, 1 - latest.spread_percent / config.maximum_spread_percent)
    atr_compactness = max(0.0, 1 - latest.spread_atr / config.maximum_spread_atr)
    persistence = min(1.0, compressed_bars / 8)
    contraction = max(0.0, min(1.0, 1 - contraction_ratio))
    release_quality = 1.0 if direction != "neutral" else 0.0
    score_components = {
        "price_compactness": round(compactness, 6),
        "atr_compactness": round(atr_compactness, 6),
        "persistence": round(persistence, 6),
        "contraction": round(contraction, 6),
        "directional_release": release_quality,
        "volume_confirmation": round(min(1.0, max(0.0, (volume_ratio or 1) - 1)), 6),
    }
    score = (
        0.25 * compactness + 0.2 * atr_compactness + 0.2 * persistence
        + 0.15 * contraction + 0.15 * release_quality
        + 0.05 * score_components["volume_confirmation"]
    )
    return MovingAverageConvergencePattern(
        state=state,
        display_name={
            MovingAverageConvergenceState.CONVERGING: "均线收敛粘合",
            MovingAverageConvergenceState.COMPRESSED: "均线粘合",
            MovingAverageConvergenceState.BULLISH_EXPANSION: "均线粘合向上发散",
            MovingAverageConvergenceState.BEARISH_EXPANSION: "均线粘合向下发散",
        }[state],
        direction=direction,
        periods=config.periods,
        start_date=bars[run_start].period_end,
        end_date=bars[-1].period_end,
        available_date=bars[available_index].period_end,
        event_date=event_date,
        upper_start=start.upper,
        lower_start=start.lower,
        upper_end=latest.upper,
        lower_end=latest.lower,
        center_start=start.center,
        center_end=latest.center,
        current_values=latest.values,
        band_points=tuple(
            (bars[index].period_end, by_index[index].upper, by_index[index].lower)
            for index in range(run_start, latest.index + 1)
            if index in by_index
        ),
        spread_percent=latest.spread_percent,
        spread_atr=latest.spread_atr,
        contraction_ratio=contraction_ratio,
        compressed_bars=compressed_bars,
        volume_ratio=volume_ratio,
        invalidation_price=latest.upper if direction == "bearish" else latest.lower,
        score=round(max(0.0, min(1.0, score)), 6),
        score_components=score_components,
    )


def _band_points(
    bars: Sequence[AnalysisBar],
    config: MovingAverageConvergenceConfig,
) -> list[_BandPoint]:
    prefix = [0.0]
    for bar in bars:
        prefix.append(prefix[-1] + bar.close)
    true_ranges = []
    for index, bar in enumerate(bars):
        previous_close = bars[index - 1].close if index else bar.close
        true_ranges.append(max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
        ))
    result = []
    for index in range(max(config.periods) - 1, len(bars)):
        values = tuple(
            (prefix[index + 1] - prefix[index + 1 - period]) / period
            for period in config.periods
        )
        upper, lower = max(values), min(values)
        center = sum(values) / len(values)
        spread = upper - lower
        atr_start = max(0, index - 13)
        atr = sum(true_ranges[atr_start:index + 1]) / (index - atr_start + 1)
        spread_percent = spread / max(abs(center), 1e-9)
        spread_atr = spread / max(atr, 1e-9)
        result.append(_BandPoint(
            index, values, upper, lower, center, spread_percent, spread_atr,
            spread_percent <= config.maximum_spread_percent
            and spread_atr <= config.maximum_spread_atr,
        ))
    return result


def _recent_compressed_end(
    points: Sequence[_BandPoint], latest_index: int, recent_release_bars: int
) -> int | None:
    for point in reversed(points):
        distance = latest_index - point.index
        if distance > recent_release_bars:
            break
        if distance > 0 and point.compressed:
            return point.index
    return None


def _contraction_ratio(
    points: Sequence[_BandPoint], latest_index: int,
    config: MovingAverageConvergenceConfig,
) -> float:
    current = [
        point.spread_percent for point in points
        if latest_index - 2 <= point.index <= latest_index
    ]
    prior = [
        point.spread_percent for point in points
        if latest_index - config.contraction_lookback_bars <= point.index < latest_index - 2
    ]
    if not current or not prior or median(prior) <= 1e-9:
        return 1.0
    return round(median(current) / median(prior), 6)


def _release_direction(
    points: Sequence[_BandPoint], bars: Sequence[AnalysisBar], run_end: int,
    config: MovingAverageConvergenceConfig,
) -> tuple[str, int] | None:
    by_index = {point.index: point for point in points}
    for index in range(run_end + 1, len(bars)):
        point = by_index.get(index)
        prior = by_index.get(index - config.slope_lookback_bars)
        if point is None or prior is None:
            continue
        bullish_order = all(
            left > right for left, right in zip(point.values, point.values[1:])
        )
        bearish_order = all(
            left < right for left, right in zip(point.values, point.values[1:])
        )
        bullish_slopes = point.values[0] > prior.values[0] and point.values[-1] >= prior.values[-1]
        bearish_slopes = point.values[0] < prior.values[0] and point.values[-1] <= prior.values[-1]
        if (
            bullish_order and bullish_slopes
            and bars[index].close > point.upper * (1 + config.release_buffer_percent)
        ):
            direction = "bullish"
        elif (
            bearish_order and bearish_slopes
            and bars[index].close < point.lower * (1 - config.release_buffer_percent)
        ):
            direction = "bearish"
        else:
            continue
        latest = points[-1]
        latest_ordered = all(
            (left > right if direction == "bullish" else left < right)
            for left, right in zip(latest.values, latest.values[1:])
        )
        latest_outside = (
            bars[-1].close > latest.upper if direction == "bullish"
            else bars[-1].close < latest.lower
        )
        return (direction, index) if latest_ordered and latest_outside else None
    return None


def _volume_ratio(bars: Sequence[AnalysisBar], lookback: int) -> float | None:
    history = [bar.volume for bar in bars[-lookback - 1:-1] if bar.volume > 0]
    if not history or bars[-1].volume <= 0:
        return None
    return round(bars[-1].volume / median(history), 6)
