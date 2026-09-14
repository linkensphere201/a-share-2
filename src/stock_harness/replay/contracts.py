"""Versioned contracts shared by every analysis-system replay."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import isfinite
from datetime import date
from typing import Literal, Mapping


EVALUATION_CONTRACT_VERSION = "analysis-outcome-evaluation-v2"
DEFAULT_EVALUATION_HORIZONS = (10, 60, 120)
Direction = Literal["long", "short"]


@dataclass(frozen=True, slots=True)
class ExitPlan:
    """System-owned frozen prices; the shared simulator owns execution semantics."""

    policy_id: str
    stop_price: float
    target_price: float
    maximum_holding_sessions: int | None = None
    round_trip_cost_bps: float = 0.0

    def validate(self) -> None:
        if not self.policy_id or any(
            not isfinite(value) or value <= 0
            for value in (self.stop_price, self.target_price)
        ):
            raise ValueError("exit plan requires finite positive boundaries and identity")
        if self.maximum_holding_sessions is not None and (
            type(self.maximum_holding_sessions) is not int
            or self.maximum_holding_sessions <= 0
        ):
            raise ValueError("maximum holding must be a positive integer")
        if not isfinite(self.round_trip_cost_bps) or self.round_trip_cost_bps < 0:
            raise ValueError("execution costs must be finite and nonnegative")


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
    exit_plan: ExitPlan | None = None

    def resolved_exit_plan(self) -> ExitPlan | None:
        if self.exit_plan is not None:
            return self.exit_plan
        if self.invalidation_price is None or self.selected_target_price is None:
            return None
        holding = self.metadata.get("maximum_holding_sessions")
        return ExitPlan(
            f"{self.system_id}:frozen-boundaries-v1",
            self.invalidation_price, self.selected_target_price,
            holding if type(holding) is int and holding > 0 else None,
        )

    def validate(self) -> None:
        if (plan := self.resolved_exit_plan()) is not None:
            plan.validate()
        if not self.system_id or not self.system_version:
            raise ValueError("frozen signal requires system identity")
        if not self.symbol or self.scope not in {"market", "board", "stock"}:
            raise ValueError("frozen signal requires a supported entity identity")
        if self.direction not in {"long", "short"} or not isfinite(self.reference_close) or self.reference_close <= 0:
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
            "exit_plan": asdict(plan) if (plan := self.resolved_exit_plan()) else None,
        }
