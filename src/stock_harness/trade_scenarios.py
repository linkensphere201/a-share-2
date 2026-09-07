"""Shared analytical trade-scenario contracts and arithmetic."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TradeDirection(StrEnum):
    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True, slots=True)
class TradeTarget:
    price: float
    basis: str
    risk_reward_ratio: float | None


@dataclass(frozen=True, slots=True)
class TradeScenario:
    method: str
    direction: TradeDirection
    entry_price: float
    invalidation_price: float
    targets: tuple[TradeTarget, ...]
    evidence_item_ids: tuple[str, ...] = ()
    analytical_only: bool = True


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
