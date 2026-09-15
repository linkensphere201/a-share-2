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
STRATEGY_VERSION = "volume-accumulation-20d-v7"
STATE = "accumulating"
VolumeAccumulationConfig = AccumulationPatternConfig


@dataclass(frozen=True, slots=True)
class VolumeAccumulationSignal:
    score: float
    evidence: dict[str, object]


def accumulation_rank_key(signal: VolumeAccumulationSignal) -> tuple[bool, float, float]:
    compact = signal.evidence.get("compact_platform", {})
    qualified = signal.evidence.get("platform_style") == "compact-platform"
    bonus = 5.0 if signal.evidence.get("recognition", {}).get("tags") else 0.0
    return qualified, (float(compact.get("score", 0)) if qualified else signal.score) + bonus, signal.score


def detect_volume_accumulation(
    bars: Sequence[AnalysisBar],
    *,
    limit_up_dates: frozenset[str] | None = None,
    config: AccumulationPatternConfig = AccumulationPatternConfig(),
) -> VolumeAccumulationSignal | None:
    """Adapt the shared accumulation pattern into the screener result contract."""
    pattern = detect_accumulation_pattern(
        bars, limit_up_dates=limit_up_dates, config=config,
    )
    if pattern is None or pattern.stage not in {"accumulation", "pending-digestion"}:
        return None
    return VolumeAccumulationSignal(score=pattern.score, evidence={
        "contract_version": STRATEGY_VERSION,
        "window": pattern.evidence["platform_sessions"],
        "window_start_date": pattern.start_date,
        "latest_close": bars[-1].close,
        "range_lower": pattern.lower,
        "range_upper": pattern.upper,
        **pattern.evidence,
    })
