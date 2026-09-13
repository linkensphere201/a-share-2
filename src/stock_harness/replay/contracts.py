"""Versioned contracts shared by every analysis-system replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Mapping


EVALUATION_CONTRACT_VERSION = "analysis-outcome-evaluation-v1"
DEFAULT_EVALUATION_HORIZONS = (10, 60, 120)
Direction = Literal["long", "short"]


@dataclass(frozen=True, slots=True)
class FrozenSignal:
    """A point-in-time selection frozen before any future bars are inspected."""

    system_id: str
    system_version: str
    symbol: str
    scope: str
    signal_date: date
    direction: Direction
    reference_close: float
    score: float | None = None
    setup_family: str | None = None
    invalidation_price: float | None = None
    selected_target_price: float | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.system_id or not self.system_version:
            raise ValueError("frozen signal requires system identity")
        if not self.symbol or self.scope not in {"market", "board", "stock"}:
            raise ValueError("frozen signal requires a supported entity identity")
        if self.direction not in {"long", "short"} or self.reference_close <= 0:
            raise ValueError("frozen signal has invalid direction or reference close")
        if self.score is not None and not 0 <= self.score <= 100:
            raise ValueError("frozen signal score must be between zero and 100")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "system_id": self.system_id,
            "system_version": self.system_version,
            "symbol": self.symbol,
            "scope": self.scope,
            "signal_date": self.signal_date.isoformat(),
            "direction": self.direction,
            "reference_close": self.reference_close,
            "score": self.score,
            "setup_family": self.setup_family,
            "invalidation_price": self.invalidation_price,
            "selected_target_price": self.selected_target_price,
            "metadata": dict(self.metadata),
        }
