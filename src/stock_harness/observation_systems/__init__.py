"""Versioned observation-system plugins used by signal review."""

from stock_harness.observation_systems.board_systems import (
    BOARD_HOTSPOT_SYSTEM,
    BoardHotspotSystem,
    TrendBreakoutSystem,
)
from stock_harness.observation_systems.contracts import (
    ObservationSystemContext,
    ObservationSystemExecution,
    ObservationSystemPlugin,
)
from stock_harness.observation_systems.registry import ObservationSystemRegistry

__all__ = [
    "BOARD_HOTSPOT_SYSTEM",
    "BoardHotspotSystem",
    "ObservationSystemContext",
    "ObservationSystemExecution",
    "ObservationSystemPlugin",
    "ObservationSystemRegistry",
    "TrendBreakoutSystem",
]
