"""Shared volatility-normalized tolerances for classic pattern families."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.classic_patterns import PatternConfig
from stock_harness.consolidation_patterns import ConsolidationConfig
from stock_harness.diamond_patterns import DiamondConfig
from stock_harness.reversal_patterns import ReversalConfig


@dataclass(frozen=True, slots=True)
class PatternToleranceProfile:
    volatility_ratio: float
    sample_size: int
    breakout_buffer_percent: float
    invalidation_buffer_percent: float
    endpoint_tolerance_percent: float
    prominence_percent: float
    flat_change_percent: float
    directional_change_percent: float
    boundary_residual_percent: float
    diamond_boundary_change_percent: float
    v_move_percent: float
    shoulder_tolerance_percent: float
    head_prominence_percent: float
    method: str = "median true-range / previous close, bounded by family"

    def classic_config(self) -> PatternConfig:
        return PatternConfig(
            endpoint_tolerance_percent=self.endpoint_tolerance_percent,
            minimum_prominence_percent=self.prominence_percent,
            breakout_buffer_percent=self.breakout_buffer_percent,
            invalidation_buffer_percent=self.invalidation_buffer_percent,
        )

    def consolidation_config(self) -> ConsolidationConfig:
        return ConsolidationConfig(
            flat_change_percent=self.flat_change_percent,
            directional_change_percent=self.directional_change_percent,
            maximum_boundary_residual_percent=self.boundary_residual_percent,
            breakout_buffer_percent=self.breakout_buffer_percent,
        )

    def diamond_config(self) -> DiamondConfig:
        return DiamondConfig(
            minimum_boundary_change_percent=self.diamond_boundary_change_percent,
            maximum_boundary_residual_percent=self.boundary_residual_percent,
            breakout_buffer_percent=self.breakout_buffer_percent,
        )

    def reversal_config(self) -> ReversalConfig:
        return ReversalConfig(
            minimum_v_move_percent=self.v_move_percent,
            shoulder_tolerance_percent=self.shoulder_tolerance_percent,
            minimum_head_prominence_percent=self.head_prominence_percent,
            breakout_buffer_percent=self.breakout_buffer_percent,
            invalidation_buffer_percent=self.invalidation_buffer_percent,
        )

    def payload(self) -> dict[str, object]:
        return asdict(self)


def build_pattern_tolerance_profile(
    bars: Sequence[AnalysisBar],
    *,
    maximum_sample_bars: int = 60,
) -> PatternToleranceProfile:
    ordered = sorted(bars, key=lambda item: item.period_end)[-maximum_sample_bars:]
    ratios = [
        max(
            bar.high - bar.low,
            abs(bar.high - previous.close),
            abs(bar.low - previous.close),
        ) / max(abs(previous.close), 1e-9)
        for previous, bar in zip(ordered, ordered[1:])
        if previous.close != 0
    ]
    volatility = _bounded(median(ratios) if ratios else 0.02, 0.01, 0.12)
    flat = _bounded(volatility * 1.5, 0.02, 0.08)
    return PatternToleranceProfile(
        volatility_ratio=round(volatility, 6),
        sample_size=len(ratios),
        breakout_buffer_percent=round(
            _bounded(volatility * 0.2, 0.003, 0.015), 6
        ),
        invalidation_buffer_percent=round(
            _bounded(volatility * 0.5, 0.008, 0.03), 6
        ),
        endpoint_tolerance_percent=round(
            _bounded(volatility * 1.5, 0.03, 0.10), 6
        ),
        prominence_percent=round(
            _bounded(volatility * 2.5, 0.05, 0.25), 6
        ),
        flat_change_percent=round(flat, 6),
        directional_change_percent=round(
            _bounded(flat * 1.4, 0.035, 0.12), 6
        ),
        boundary_residual_percent=round(
            _bounded(volatility * 1.5, 0.02, 0.08), 6
        ),
        diamond_boundary_change_percent=round(
            _bounded(volatility * 1.2, 0.02, 0.08), 6
        ),
        v_move_percent=round(
            _bounded(volatility * 3.0, 0.06, 0.25), 6
        ),
        shoulder_tolerance_percent=round(
            _bounded(volatility * 2.0, 0.04, 0.12), 6
        ),
        head_prominence_percent=round(
            _bounded(volatility * 2.5, 0.05, 0.20), 6
        ),
    )


def _bounded(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
