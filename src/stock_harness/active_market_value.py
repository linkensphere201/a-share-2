"""Causal daily active-market-value calculation."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


ALGORITHM_VERSION = "sh-amv-cyf-v1"
DEFAULT_DEFINITION_ID = "sh-amv-cyf-v1"
DEFAULT_SYMBOL = "SHAMV.A"
DEFAULT_NAME = "StockHarness 活跃市值"


@dataclass(frozen=True, slots=True)
class ActiveMarketValueInput:
    instrument_id: int
    turnover_rate_f: float
    free_share: float
    feature_close: float
    open: float | None
    high: float | None
    low: float | None
    close: float | None


@dataclass(frozen=True, slots=True)
class ActiveMarketValueBar:
    trade_date: int
    absolute_open: float
    absolute_high: float
    absolute_low: float
    absolute_close: float
    open: float
    high: float
    low: float
    close: float
    eligible_count: int
    total_count: int
    coverage_ratio: float


class ActiveMarketValueCalculator:
    def __init__(
        self, smoothing_period: int = 13, scale_k: float = 100,
        turnover_cap: float = 1, initial_state: dict[int, float] | None = None,
    ) -> None:
        if smoothing_period <= 0 or scale_k <= 0 or turnover_cap <= 0:
            raise ValueError("active-market-value parameters must be positive")
        self.alpha = 2 / (smoothing_period + 1)
        self.scale_k = scale_k
        self.turnover_cap = turnover_cap
        self.state: dict[int, float] = dict(initial_state or {})

    def calculate_absolute(
        self, trade_date: int, rows: list[ActiveMarketValueInput],
        expected_count: int | None = None,
    ) -> tuple[int, tuple[float, float, float, float, int, int, float]] | None:
        absolute_open = absolute_high = absolute_low = absolute_close = 0.0
        covered_value = total_value = 0.0
        eligible = 0
        for row in rows:
            total_value += row.free_share * row.feature_close
            turnover = min(self.turnover_cap, max(0.0, row.turnover_rate_f / 100))
            previous = self.state.get(row.instrument_id)
            smoothed = turnover if previous is None else self.alpha * turnover + (1 - self.alpha) * previous
            self.state[row.instrument_id] = smoothed
            prices = (row.open, row.high, row.low, row.close)
            if any(value is None or not isfinite(value) or value <= 0 for value in prices):
                continue
            activity = 1 - 1 / (1 + self.scale_k * smoothed)
            active_shares = row.free_share * activity
            absolute_open += active_shares * float(row.open)
            absolute_high += active_shares * float(row.high)
            absolute_low += active_shares * float(row.low)
            absolute_close += active_shares * float(row.close)
            covered_value += row.free_share * row.feature_close
            eligible += 1
        if eligible == 0 or absolute_close <= 0:
            return None
        total_count = max(len(rows), expected_count or 0)
        value_coverage = covered_value / total_value if total_value > 0 else 0.0
        count_coverage = eligible / total_count if total_count else 0.0
        coverage = min(value_coverage, count_coverage)
        return trade_date, (
            absolute_open, absolute_high, absolute_low, absolute_close,
            eligible, total_count, min(1.0, max(0.0, coverage)),
        )

    def active_close_components(
        self, rows: list[ActiveMarketValueInput]
    ) -> dict[int, float]:
        components: dict[int, float] = {}
        for row in rows:
            smoothed = self.state.get(row.instrument_id)
            if smoothed is None or row.close is None or not isfinite(row.close) or row.close <= 0:
                continue
            activity = 1 - 1 / (1 + self.scale_k * smoothed)
            components[row.instrument_id] = row.free_share * activity * row.close
        return components


def rank_active_value_contributions(
    current: dict[int, float], previous: dict[int, float], limit: int = 10,
) -> tuple[list[tuple[int, float, float]], list[tuple[int, float, float]]]:
    if limit <= 0:
        raise ValueError("active-market-value contribution limit must be positive")
    changes = [
        (instrument_id, current.get(instrument_id, 0.0), current.get(instrument_id, 0.0) - prior)
        for instrument_id, prior in previous.items()
    ]
    for instrument_id, active_close in current.items():
        if instrument_id not in previous:
            changes.append((instrument_id, active_close, active_close))
    positive = sorted(
        (item for item in changes if item[2] > 0), key=lambda item: (-item[2], item[0])
    )[:limit]
    negative = sorted(
        (item for item in changes if item[2] < 0), key=lambda item: (item[2], item[0])
    )[:limit]
    return positive, negative


def normalize_bars(
    rows: list[tuple[int, tuple[float, float, float, float, int, int, float]]],
    base_value: float, base_close: float | None = None,
) -> list[ActiveMarketValueBar]:
    if not rows:
        return []
    normalization_close = base_close or rows[0][1][3]
    scale = base_value / normalization_close
    return [ActiveMarketValueBar(
        trade_date=trade_date,
        absolute_open=values[0], absolute_high=values[1],
        absolute_low=values[2], absolute_close=values[3],
        open=values[0] * scale, high=values[1] * scale,
        low=values[2] * scale, close=values[3] * scale,
        eligible_count=values[4], total_count=values[5], coverage_ratio=values[6],
    ) for trade_date, values in rows]
