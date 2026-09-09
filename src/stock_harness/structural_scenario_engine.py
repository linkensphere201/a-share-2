"""Deterministic trade scenarios derived from persisted M4 structures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.analysis_results import GeneratedAnalysisItem, GeneratedItemType
from stock_harness.overhead_supply import SUPPLY_ITEM_ID, build_overhead_supply_item
from stock_harness.structural_map import (
    StructuralBoundary,
    StructuralSetup,
    build_structural_map,
)
from stock_harness.trade_scenarios import TradeDirection, calculate_risk_reward


STRUCTURAL_SCENARIO_VERSION = "structural-trade-scenario-v2"


@dataclass(frozen=True, slots=True)
class _TargetCandidate:
    price: float
    basis: str
    source_item_ids: tuple[str, ...]
    score: float


def build_structural_scenario_items(
    bars: Sequence[AnalysisBar],
    items: Sequence[GeneratedAnalysisItem],
    *,
    minimum_risk_reward: float = 1.5,
) -> list[GeneratedAnalysisItem]:
    if len(bars) < 2:
        return []
    range_items = _range_reference_items(bars)
    supply_item = build_overhead_supply_item(bars, items)
    structural = build_structural_map(
        [*items, *range_items, supply_item], bars[-1].close
    )
    atr = _latest_atr(bars)
    scenarios = []
    for rank, setup in enumerate(structural.setups[:3], start=1):
        scenario = _build_scenario(
            bars, structural.boundaries, setup, atr,
            supply_score=float(supply_item.payload["score"]),
            rank=rank, minimum_risk_reward=minimum_risk_reward,
        )
        if scenario is not None:
            scenarios.append(scenario)
    return [*range_items, supply_item, *scenarios]


def _build_scenario(
    bars: Sequence[AnalysisBar],
    boundaries: Sequence[StructuralBoundary],
    setup: StructuralSetup,
    atr: float,
    *,
    supply_score: float,
    rank: int,
    minimum_risk_reward: float,
) -> GeneratedAnalysisItem | None:
    direction = TradeDirection(setup.direction)
    policy = _setup_policy(setup)
    buffer = max(atr * policy["buffer_atr"], setup.boundary_price * 0.003)
    entry = (
        setup.boundary_price + buffer
        if direction is TradeDirection.LONG
        else setup.boundary_price - buffer
    )
    invalidation, invalidation_sources = _invalidation(
        boundaries, setup, entry, direction, buffer
    )
    if invalidation is None:
        return None
    risk = entry - invalidation if direction is TradeDirection.LONG else invalidation - entry
    if risk <= 0:
        return None
    targets = _targets(boundaries, setup, entry, direction, atr)
    state = _scenario_state(
        setup.state, bars[-1].close, entry, atr, risk, direction
    )
    target_payloads = []
    for index, target in enumerate(targets[:3], start=1):
        raw_rr = calculate_risk_reward(direction, entry, invalidation, target.price)
        stressed_rr = _stressed_risk_reward(
            direction, entry, invalidation, target.price, supply_score
        )
        target_payloads.append({
            "label": f"T{index}",
            "price": round(target.price, 6),
            "basis": target.basis,
            "side": "upside" if direction is TradeDirection.LONG else "downside",
            "risk_reward_ratio": raw_rr,
            "stressed_risk_reward_ratio": stressed_rr,
            "evidence_item_ids": list(target.source_item_ids),
            "score": round(target.score, 6),
        })
    if not target_payloads and state not in {"invalidated", "no-entry"}:
        state = "no-entry"
    primary_target = next((target for target in target_payloads if (
        isinstance(target["stressed_risk_reward_ratio"], (int, float))
        and float(target["stressed_risk_reward_ratio"]) >= minimum_risk_reward
    )), target_payloads[0] if target_payloads else None)
    evidence_ids = tuple(dict.fromkeys((
        setup.source_item_id,
        SUPPLY_ITEM_ID,
        *invalidation_sources,
        *(source for target in target_payloads for source in target["evidence_item_ids"]),
    )))
    risk_percent = abs(entry - invalidation) / entry * 100
    return GeneratedAnalysisItem(
        item_id=f"structural-trade-scenario-{rank}",
        item_type=GeneratedItemType.SCENARIO,
        payload={
            "kind": "structural-trade-scenario",
            "contract_version": STRUCTURAL_SCENARIO_VERSION,
            "profile": "structural",
            "rank": rank,
            "primary": rank == 1,
            "direction": direction.value,
            "state": state,
            "setup_family": setup.family,
            "horizon": setup.horizon,
            "as_of_date": bars[-1].period_end.isoformat(),
            "start_date": setup.start_date or bars[max(0, len(bars) - 20)].period_end.isoformat(),
            "reference_price": round(bars[-1].close, 6),
            "entry_price": round(entry, 6),
            "entry_range": {
                "lower": round(setup.boundary_price - buffer, 6),
                "upper": round(setup.boundary_price + buffer, 6),
            },
            "invalidation_price": round(invalidation, 6),
            "risk_percent": round(risk_percent, 4),
            "targets": target_payloads,
            "selected_target_label": primary_target["label"] if primary_target else None,
            "minimum_risk_reward": minimum_risk_reward,
            "has_trade_space": primary_target is not None and any(
                isinstance(target["stressed_risk_reward_ratio"], (int, float))
                and float(target["stressed_risk_reward_ratio"]) >= minimum_risk_reward
                for target in target_payloads
            ),
            "setup_basis": f"{setup.horizon} {setup.family} structural boundary",
            "confirmation_rule": policy["confirmation_rule"],
            "entry_policy": policy["name"],
            "invalidation_basis": "nearest independent structural support/resistance",
            "assumptions": [
                "trigger buffer is max(0.2 ATR, 0.3% of boundary price)",
                "targets use conservative near edges of independent structure clusters",
                "stress RR includes 0.1% entry slippage and 0.2% target haircut",
            ],
            "uncertainty": [
                "daily OHLCV structures are analytical estimates, not execution prices",
                "historical volume-at-price is not actual holder cost distribution",
            ],
            "evidence_item_ids": list(evidence_ids),
            "invalidation_evidence_item_ids": list(invalidation_sources),
            "overhead_supply_item_id": SUPPLY_ITEM_ID,
            "overhead_supply_score": round(supply_score, 6),
            "analytical_only": True,
        },
    )


def _invalidation(
    boundaries: Sequence[StructuralBoundary],
    setup: StructuralSetup,
    entry: float,
    direction: TradeDirection,
    buffer: float,
) -> tuple[float | None, tuple[str, ...]]:
    explicit = setup.invalidation_price
    if explicit is not None and (
        (direction is TradeDirection.LONG and 0 < explicit < entry)
        or (direction is TradeDirection.SHORT and explicit > entry)
    ):
        return (
            max(0.000001, explicit - buffer)
            if direction is TradeDirection.LONG
            else explicit + buffer,
            (setup.source_item_id,),
        )
    candidates = [boundary for boundary in boundaries if (
        boundary.source_item_id != setup.source_item_id
        and (
            direction is TradeDirection.LONG
            and boundary.role in {"support", "neutral"}
            and boundary.upper < entry
            or direction is TradeDirection.SHORT
            and boundary.role in {"resistance", "neutral"}
            and boundary.lower > entry
        )
    )]
    if not candidates:
        return None, ()
    if direction is TradeDirection.LONG:
        selected = max(candidates, key=lambda value: value.upper)
        return max(0.000001, selected.lower - buffer), (selected.source_item_id,)
    selected = min(candidates, key=lambda value: value.lower)
    return selected.upper + buffer, (selected.source_item_id,)


def _targets(
    boundaries: Sequence[StructuralBoundary],
    setup: StructuralSetup,
    entry: float,
    direction: TradeDirection,
    atr: float,
) -> list[_TargetCandidate]:
    tolerance = max(atr * 0.5, entry * 0.005)
    raw: list[_TargetCandidate] = []
    for boundary in boundaries:
        if boundary.source_item_id == setup.source_item_id:
            continue
        checkpoint = {"short": 5, "medium": 10, "long": 20}.get(setup.horizon, 10)
        projected = boundary.center + boundary.slope_per_bar * checkpoint
        price = (
            projected if boundary.slope_per_bar
            else boundary.lower if direction is TradeDirection.LONG else boundary.upper
        )
        if (
            direction is TradeDirection.LONG and price <= entry + tolerance
            or direction is TradeDirection.SHORT and price >= entry - tolerance
        ):
            continue
        raw.append(_TargetCandidate(
            price,
            (
                f"{boundary.source_kind}-projection-{checkpoint}d"
                if boundary.slope_per_bar else boundary.source_kind
            ),
            (boundary.source_item_id,), boundary.score
        ))
    raw.sort(key=lambda value: value.price, reverse=direction is TradeDirection.SHORT)
    clusters: list[list[_TargetCandidate]] = []
    used_sources: set[str] = set()
    for candidate in raw:
        if any(source in used_sources for source in candidate.source_item_ids):
            continue
        used_sources.update(candidate.source_item_ids)
        if clusters and abs(candidate.price - clusters[-1][0].price) <= tolerance:
            clusters[-1].append(candidate)
        else:
            clusters.append([candidate])
    result = []
    for cluster in clusters:
        price = min(value.price for value in cluster) if direction is TradeDirection.LONG else max(value.price for value in cluster)
        sources = tuple(dict.fromkeys(
            source for value in cluster for source in value.source_item_ids
        ))
        kinds = "+".join(sorted({value.basis for value in cluster}))
        result.append(_TargetCandidate(
            price, kinds, sources,
            max(value.score for value in cluster) + min(0.2, 0.05 * (len(sources) - 1)),
        ))
    return result


def _scenario_state(
    source_state: str,
    latest_close: float,
    entry: float,
    atr: float,
    risk: float,
    direction: TradeDirection,
) -> str:
    if source_state in {"failed", "invalidated"}:
        return "invalidated"
    triggered = source_state in {"triggered", "confirmed", "continuing", "retesting"}
    if source_state == "retesting":
        return "retest"
    extension = latest_close - entry if direction is TradeDirection.LONG else entry - latest_close
    if triggered and extension > max(2 * atr, 1.5 * risk):
        return "extended"
    return "triggered" if triggered else "waiting-trigger"


def _range_reference_items(
    bars: Sequence[AnalysisBar],
) -> list[GeneratedAnalysisItem]:
    result = []
    prior = bars[:-1]
    for horizon in (20, 60, 120):
        visible = prior[-horizon:]
        if len(visible) < min(10, horizon):
            continue
        high_bar = max(visible, key=lambda value: value.high)
        low_bar = min(visible, key=lambda value: value.low)
        result.append(GeneratedAnalysisItem(
            item_id=f"structural-range-{horizon}",
            item_type=GeneratedItemType.EVIDENCE,
            payload={
                "kind": "structural-range-reference",
                "horizon_bars": horizon,
                "low": low_bar.low,
                "low_date": low_bar.period_end.isoformat(),
                "high": high_bar.high,
                "high_date": high_bar.period_end.isoformat(),
                "method": "prior completed visible bars; latest bar excluded",
            },
        ))
    return result


def _latest_atr(bars: Sequence[AnalysisBar], period: int = 14) -> float:
    selected = bars[-(period + 1):]
    ranges = []
    for index, bar in enumerate(selected):
        previous_close = selected[index - 1].close if index else bar.open
        ranges.append(max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
        ))
    value = sum(ranges) / len(ranges) if ranges else bars[-1].close * 0.02
    return max(value, bars[-1].close * 0.002)


def _stressed_risk_reward(
    direction: TradeDirection,
    entry: float,
    invalidation: float,
    target: float,
    supply_score: float,
) -> float | None:
    entry_slippage = 0.001 + 0.001 * supply_score
    target_haircut = 0.002 + 0.003 * supply_score
    if direction is TradeDirection.LONG:
        stressed_entry = entry * (1 + entry_slippage)
        stressed_target = target * (1 - target_haircut)
    else:
        stressed_entry = entry * (1 - entry_slippage)
        stressed_target = target * (1 + target_haircut)
    return calculate_risk_reward(
        direction, stressed_entry, invalidation, stressed_target
    )


def _setup_policy(setup: StructuralSetup) -> dict[str, object]:
    family = setup.family.lower()
    if setup.state == "retesting":
        return {
            "name": "observed-retest-hold",
            "buffer_atr": 0.1,
            "confirmation_rule": "price touched the broken boundary and closed on the holding side",
        }
    if any(value in family for value in (
        "double", "head-and-shoulders", "v-bottom", "v-top", "reversal",
    )):
        return {
            "name": "reversal-neckline-confirmation",
            "buffer_atr": 0.25,
            "confirmation_rule": "close confirms the reversal neckline with structural low/high intact",
        }
    if any(value in family for value in ("flag", "triangle", "rectangle", "diamond")):
        return {
            "name": "bounded-pattern-breakout",
            "buffer_atr": 0.2,
            "confirmation_rule": "close clears the active pattern boundary; retest hold upgrades confidence",
        }
    return {
        "name": "trend-boundary-break",
        "buffer_atr": 0.2,
        "confirmation_rule": "close clears the projected trend boundary without invalidating structure",
    }
