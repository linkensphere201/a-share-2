"""Shared analytical trade-scenario contracts and arithmetic."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence


TRADE_SCENARIO_VERSION = "trade-scenario-v1"


class TradeDirection(StrEnum):
    LONG = "long"
    SHORT = "short"


class TradeTargetSide(StrEnum):
    UPSIDE = "upside"
    DOWNSIDE = "downside"


@dataclass(frozen=True, slots=True)
class TradeTarget:
    price: float
    basis: str
    side: TradeTargetSide
    risk_reward_ratio: float | None


@dataclass(frozen=True, slots=True)
class TradeScenario:
    contract_version: str
    method: str
    direction: TradeDirection
    entry_price: float | None
    invalidation_price: float | None
    targets: tuple[TradeTarget, ...]
    setup_basis: str | None = None
    minimum_risk_reward: float = 1.5
    assumptions: tuple[str, ...] = ()
    evidence_item_ids: tuple[str, ...] = ()
    analytical_only: bool = True

    @property
    def has_trade_space(self) -> bool:
        return any(
            target.risk_reward_ratio is not None
            and target.risk_reward_ratio >= self.minimum_risk_reward
            for target in self.targets
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "method": self.method,
            "direction": self.direction.value,
            "entry_price": self.entry_price,
            "invalidation_price": self.invalidation_price,
            "targets": [
                {
                    "price": target.price,
                    "basis": target.basis,
                    "side": target.side.value,
                    "risk_reward_ratio": target.risk_reward_ratio,
                }
                for target in self.targets
            ],
            "setup_basis": self.setup_basis,
            "minimum_risk_reward": self.minimum_risk_reward,
            "has_trade_space": self.has_trade_space,
            "assumptions": list(self.assumptions),
            "evidence_item_ids": list(self.evidence_item_ids),
            "analytical_only": self.analytical_only,
        }


def build_trade_scenario(
    *,
    method: str,
    direction: TradeDirection,
    entry_price: float | None,
    invalidation_price: float | None,
    targets: Sequence[tuple[float, str, TradeTargetSide]],
    setup_basis: str | None = None,
    minimum_risk_reward: float = 1.5,
    assumptions: Sequence[str] = (),
    evidence_item_ids: Sequence[str] = (),
) -> TradeScenario:
    resolved = tuple(
        TradeTarget(
            price=price,
            basis=basis,
            side=side,
            risk_reward_ratio=(
                calculate_risk_reward(
                    direction, entry_price, invalidation_price, price
                )
                if entry_price is not None and invalidation_price is not None
                else None
            ),
        )
        for price, basis, side in targets
    )
    return TradeScenario(
        contract_version=TRADE_SCENARIO_VERSION,
        method=method,
        direction=direction,
        entry_price=entry_price,
        invalidation_price=invalidation_price,
        targets=resolved,
        setup_basis=setup_basis,
        minimum_risk_reward=minimum_risk_reward,
        assumptions=tuple(assumptions),
        evidence_item_ids=tuple(evidence_item_ids),
    )


def calculate_risk_reward(
    direction: TradeDirection,
    entry_price: float,
    invalidation_price: float,
    target_price: float,
    *,
    precision: int = 6,
) -> float | None:
    if min(entry_price, invalidation_price, target_price) <= 0:
        return None
    if direction is TradeDirection.LONG:
        risk = entry_price - invalidation_price
        reward = target_price - entry_price
    else:
        risk = invalidation_price - entry_price
        reward = entry_price - target_price
    return round(reward / risk, precision) if risk > 0 and reward > 0 else None
