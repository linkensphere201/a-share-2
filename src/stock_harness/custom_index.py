"""Deterministic daily custom-index calculation primitives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import blake2b
from math import isfinite
from typing import Literal, Sequence

from stock_harness.models import CustomIndexBar, DailyBar


CALCULATION_VERSION = "daily-envelope-v1"
WeightingMethod = Literal["equal", "manual"]


@dataclass(frozen=True, slots=True)
class WeightedMember:
    symbol: str
    raw_weight: float
    normalized_weight: float


@dataclass(frozen=True, slots=True)
class ConstituentInput:
    symbol: str
    weight: float
    current: DailyBar | None
    previous_close: float | None
    current_factor: float | None
    previous_factor: float | None
    status: Literal["trading", "suspended", "inferred_suspension", "not_eligible", "missing"]


def normalize_members(
    members: Sequence[tuple[str, float | None]],
    weighting_method: WeightingMethod,
) -> tuple[WeightedMember, ...]:
    if not members:
        raise ValueError("custom index requires at least one member")
    symbols = [symbol.upper().strip() for symbol, _ in members]
    if any(not symbol for symbol in symbols):
        raise ValueError("custom index member symbol is required")
    if len(symbols) != len(set(symbols)):
        raise ValueError("custom index members must contain unique symbols")
    raw = [1.0 if weighting_method == "equal" else float(weight or 0) for _, weight in members]
    if any(not isfinite(value) or value <= 0 for value in raw):
        raise ValueError("custom index member weights must be positive finite numbers")
    total = sum(raw)
    return tuple(
        WeightedMember(symbol, value, value / total)
        for symbol, value in zip(symbols, raw, strict=True)
    )


def base_bar(trade_date: date, base_value: float, total_count: int) -> CustomIndexBar:
    if not isfinite(base_value) or base_value <= 0:
        raise ValueError("custom index base value must be positive")
    if total_count <= 0:
        raise ValueError("custom index requires at least one member")
    digest = blake2b(
        f"base|{trade_date.isoformat()}|{base_value:.12g}|{total_count}".encode("ascii"),
        digest_size=16,
    ).digest()
    return CustomIndexBar(
        trade_date, base_value, base_value, base_value, base_value,
        0.0, total_count, total_count, "complete", digest,
    )


def calculate_bar(
    trade_date: date,
    previous_index_close: float,
    constituents: Sequence[ConstituentInput],
) -> CustomIndexBar | None:
    eligible = [item for item in constituents if item.status != "not_eligible"]
    if not eligible:
        return None
    missing = [item.symbol for item in eligible if item.status == "missing"]
    if missing:
        raise ValueError("missing constituent evidence: " + ", ".join(sorted(missing)))
    effective = [item for item in eligible if item.status != "not_eligible"]
    weight_total = sum(item.weight for item in effective)
    if weight_total <= 0:
        return None

    open_return = high_return = low_return = close_return = 0.0
    inferred = False
    digest = blake2b(digest_size=16)
    for item in sorted(effective, key=lambda value: value.symbol):
        weight = item.weight / weight_total
        if item.status in {"suspended", "inferred_suspension"}:
            returns = (0.0, 0.0, 0.0, 0.0)
            inferred = inferred or item.status == "inferred_suspension"
        else:
            if (
                item.current is None
                or item.previous_close is None
                or item.previous_close <= 0
                or item.current_factor is None
                or item.previous_factor is None
                or item.current_factor <= 0
                or item.previous_factor <= 0
            ):
                raise ValueError(f"incomplete adjusted-price input: {item.symbol}")
            denominator = item.previous_close * item.previous_factor
            returns = tuple(
                price * item.current_factor / denominator - 1.0
                for price in (
                    item.current.open, item.current.high,
                    item.current.low, item.current.close,
                )
            )
        open_return += weight * returns[0]
        high_return += weight * returns[1]
        low_return += weight * returns[2]
        close_return += weight * returns[3]
        digest.update(
            (
                f"{item.symbol}|{weight:.17g}|{item.status}|"
                f"{returns[0]:.17g}|{returns[1]:.17g}|"
                f"{returns[2]:.17g}|{returns[3]:.17g}\n"
            ).encode("ascii")
        )

    index_open = previous_index_close * (1.0 + open_return)
    index_high = previous_index_close * (1.0 + high_return)
    index_low = previous_index_close * (1.0 + low_return)
    index_close = previous_index_close * (1.0 + close_return)
    index_high = max(index_high, index_open, index_close)
    index_low = min(index_low, index_open, index_close)
    if index_low <= 0 or not all(isfinite(value) for value in (
        index_open, index_high, index_low, index_close,
    )):
        raise ValueError("custom index produced an invalid price")
    return CustomIndexBar(
        trade_date=trade_date,
        open=index_open,
        high=index_high,
        low=index_low,
        close=index_close,
        daily_return=close_return,
        eligible_count=len(effective),
        total_count=len(constituents),
        quality_status="inferred_suspension" if inferred else "complete",
        input_hash=digest.digest(),
    )
