"""Causal volatility-adaptive directional-change pivots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar


class PivotKind(StrEnum):
    HIGH = "high"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class DirectionalChangeConfig:
    atr_period: int = 14
    atr_multiplier: float = 2.0
    minimum_reversal_percent: float = 0.03

    def validate(self) -> None:
        if not 2 <= self.atr_period <= 100:
            raise ValueError("ATR period must be between 2 and 100")
        if not 0.25 <= self.atr_multiplier <= 10:
            raise ValueError("ATR multiplier must be between 0.25 and 10")
        if not 0.001 <= self.minimum_reversal_percent <= 0.25:
            raise ValueError("minimum reversal percent must be between 0.1% and 25%")


@dataclass(frozen=True, slots=True)
class PricePivot:
    kind: PivotKind
    pivot_date: date
    price: float
    confirmed_date: date | None
    threshold: float
    tentative: bool


def detect_directional_change_pivots(
    bars: Sequence[AnalysisBar],
    config: DirectionalChangeConfig = DirectionalChangeConfig(),
) -> tuple[PricePivot, ...]:
    """Return alternating pivots using no bars after each confirmation date."""
    config.validate()
    if not bars:
        return ()
    ordered = sorted(bars, key=lambda item: item.period_end)
    atrs = causal_average_true_range(ordered, config.atr_period)
    low_index = high_index = 0
    direction: str | None = None
    pivots: list[PricePivot] = []

    for index, bar in enumerate(ordered):
        if bar.low < ordered[low_index].low:
            low_index = index
        if bar.high > ordered[high_index].high:
            high_index = index
        threshold = _threshold(bar.close, atrs[index], config)

        if direction is None:
            rise = bar.high - ordered[low_index].low
            fall = ordered[high_index].high - bar.low
            if low_index < index and rise >= threshold:
                pivots.append(_confirmed(PivotKind.LOW, ordered[low_index], bar, threshold))
                direction = "rising"
                high_index = index
            elif high_index < index and fall >= threshold:
                pivots.append(_confirmed(PivotKind.HIGH, ordered[high_index], bar, threshold))
                direction = "falling"
                low_index = index
            continue

        if direction == "rising":
            if bar.high >= ordered[high_index].high:
                high_index = index
            reversal = ordered[high_index].high - bar.low
            if high_index < index and reversal >= threshold:
                pivots.append(_confirmed(PivotKind.HIGH, ordered[high_index], bar, threshold))
                direction = "falling"
                low_index = index
        else:
            if bar.low <= ordered[low_index].low:
                low_index = index
            reversal = bar.high - ordered[low_index].low
            if low_index < index and reversal >= threshold:
                pivots.append(_confirmed(PivotKind.LOW, ordered[low_index], bar, threshold))
                direction = "rising"
                high_index = index

    tentative_kind = PivotKind.HIGH if direction == "rising" else PivotKind.LOW
    tentative_index = high_index if tentative_kind is PivotKind.HIGH else low_index
    last = ordered[-1]
    tentative_bar = ordered[tentative_index]
    if not pivots or (
        pivots[-1].kind is not tentative_kind
        or pivots[-1].pivot_date != tentative_bar.period_end
    ):
        pivots.append(PricePivot(
            kind=tentative_kind,
            pivot_date=tentative_bar.period_end,
            price=(tentative_bar.high if tentative_kind is PivotKind.HIGH else tentative_bar.low),
            confirmed_date=None,
            threshold=_threshold(last.close, atrs[-1], config),
            tentative=True,
        ))
    return tuple(pivots)


def _confirmed(
    kind: PivotKind, pivot_bar: AnalysisBar, confirmation_bar: AnalysisBar, threshold: float
) -> PricePivot:
    return PricePivot(
        kind=kind,
        pivot_date=pivot_bar.period_end,
        price=pivot_bar.high if kind is PivotKind.HIGH else pivot_bar.low,
        confirmed_date=confirmation_bar.period_end,
        threshold=threshold,
        tentative=False,
    )


def _threshold(
    close: float, atr: float, config: DirectionalChangeConfig
) -> float:
    return max(close * config.minimum_reversal_percent, atr * config.atr_multiplier)


def causal_average_true_range(
    bars: Sequence[AnalysisBar], period: int = 14
) -> list[float]:
    """Return a causal simple moving average of true range for every bar."""
    if period < 1:
        raise ValueError("ATR period must be positive")
    true_ranges: list[float] = []
    result: list[float] = []
    previous_close: float | None = None
    for bar in bars:
        true_range = bar.high - bar.low
        if previous_close is not None:
            true_range = max(
                true_range,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        true_ranges.append(true_range)
        window = true_ranges[-period:]
        result.append(sum(window) / len(window))
        previous_close = bar.close
    return result
