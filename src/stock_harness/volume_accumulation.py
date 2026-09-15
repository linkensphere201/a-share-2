"""Screener adapter for the shared accumulation-pattern detector."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from stock_harness.accumulation_pattern import (
    AccumulationPatternConfig,
    detect_accumulation_pattern,
)
from stock_harness.analysis_inputs import AnalysisBar


STRATEGY_ID = "volume-accumulation-20d"
STRATEGY_VERSION = "volume-accumulation-20d-v4"
STATE = "accumulating"
VolumeAccumulationConfig = AccumulationPatternConfig


@dataclass(frozen=True, slots=True)
class VolumeAccumulationSignal:
    score: float
    evidence: dict[str, object]


def detect_volume_accumulation(
    bars: Sequence[AnalysisBar],
    *,
    limit_up_dates: frozenset[str] = frozenset(),
    config: AccumulationPatternConfig = AccumulationPatternConfig(),
) -> VolumeAccumulationSignal | None:
    """Adapt the shared accumulation pattern into the screener result contract."""
    pattern = detect_accumulation_pattern(
        bars, limit_up_dates=limit_up_dates, config=config,
    )
    if pattern is None:
        return None
    return VolumeAccumulationSignal(score=pattern.score, evidence={
        "contract_version": STRATEGY_VERSION,
        "window": config.lift_window + config.platform_window,
        "window_start_date": pattern.start_date,
        "latest_close": bars[-1].close,
        "range_lower": pattern.lower,
        "range_upper": pattern.upper,
        **pattern.evidence,
    })
