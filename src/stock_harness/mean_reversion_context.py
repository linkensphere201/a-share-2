"""Causal market, relative-strength, and scope context for mean reversion."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


CONTEXT_VERSION = "mean-reversion-context-v1"


def build_market_permission(
    market_entities: Sequence[Mapping[str, object]],
    *, candidate_count: int, entity_count: int,
    liquidity: Mapping[str, object] | None = None,
) -> dict[str, object]:
    by_symbol = {str(item.get("symbol") or ""): item for item in market_entities}
    benchmark = _facts(by_symbol.get("000001.SH"))
    active = _facts(by_symbol.get("SHAMV.A"))
    benchmark_risk = _adverse_market(benchmark)
    active_risk = _adverse_market(active)
    pressure_ratio = candidate_count / entity_count if entity_count > 0 else 0.0
    crowded = candidate_count >= 20 and pressure_ratio >= .0025
    moderately_crowded = candidate_count >= 8 and pressure_ratio >= .001
    liquidity = liquidity or {}
    contracting = str(liquidity.get("direction") or "") == "contracting"
    low_capacity = str(liquidity.get("capacity_tier") or "") == "low"
    benchmark_return20 = _number(_mapping(benchmark.get("regime")).get("return20"))
    broad_risk = benchmark_risk and active_risk
    liquidity_risk = contracting and (low_capacity or benchmark_return20 < 0)
    capacity_pressure_risk = low_capacity and moderately_crowded
    available = bool(benchmark)
    if available and (
        crowded or broad_risk or liquidity_risk or capacity_pressure_risk
    ):
        status = "blocked"
    elif available and (
        crowded or moderately_crowded or benchmark_risk or active_risk or contracting
    ):
        status = "observe-only"
    elif available:
        status = "allowed"
    else:
        status = "unknown"
    return {
        "version": CONTEXT_VERSION,
        "status": status,
        "candidate_count": candidate_count,
        "entity_count": entity_count,
        "candidate_pressure_ratio": round(pressure_ratio, 6),
        "crowded": crowded,
        "benchmark_risk": benchmark_risk,
        "active_market_value_risk": active_risk,
        "liquidity_risk": liquidity_risk,
        "capacity_pressure_risk": capacity_pressure_risk,
        "liquidity": dict(liquidity),
        "causal": True,
    }


def normalize_relative_strength(entity: Mapping[str, object]) -> dict[str, object]:
    facts = _facts(entity)
    embedded = _mapping(facts.get("relative_strength"))
    source = _mapping(entity.get("relative_strength"))
    market = _mapping(source.get("market_excess")) or _mapping(embedded.get("market_excess"))
    board = _mapping(source.get("board_excess"))
    market5, market20 = _period(market, 5), _period(market, 20)
    board5, board20 = _period(board, 5), _period(board, 20)
    market_available = market5 is not None and market20 is not None
    board_available = board5 is not None and board20 is not None
    market_recovering = bool(
        market_available and market5 >= -.02 and market5 >= market20 - .01
    )
    board_recovering = bool(
        not board_available or (board5 >= -.02 and board5 >= board20 - .01)
    )
    return {
        "version": CONTEXT_VERSION,
        "available": market_available,
        "market_excess_5": market5, "market_excess_20": market20,
        "board_excess_5": board5, "board_excess_20": board20,
        "market_recovering": market_recovering,
        "board_recovering": board_recovering,
        "passed": market_recovering and board_recovering,
    }


def board_execution_context(
    entity: Mapping[str, object], capacity: Mapping[str, object] | None,
    hotspot: Mapping[str, object] | None,
) -> dict[str, object]:
    breadth = _mapping(entity.get("board_breadth"))
    capacity = capacity or {}
    hotspot = hotspot or {}
    coverage = _optional_number(breadth.get("coverage_ratio"))
    if coverage is None:
        members = _optional_number(breadth.get("member_count"))
        covered = _optional_number(breadth.get("covered_member_count"))
        coverage = covered / members if members and covered is not None else None
    breadth_value = _optional_number(breadth.get("breadth"))
    diffusion = _optional_number(hotspot.get("positive_return_5_ratio"))
    capacity_coverage = _optional_number(capacity.get("coverage_ratio"))
    available = all(value is not None for value in (
        coverage, breadth_value, diffusion, capacity_coverage,
    ))
    passed = bool(
        available and coverage >= .6 and capacity_coverage >= .6
        and breadth_value >= 0 and diffusion >= .45
    )
    return {
        "version": CONTEXT_VERSION, "available": available, "passed": passed,
        "coverage_ratio": coverage, "breadth": breadth_value,
        "positive_return_5_ratio": diffusion,
        "capacity_coverage_ratio": capacity_coverage,
    }


def _adverse_market(facts: Mapping[str, object]) -> bool:
    if not facts:
        return False
    regime = _mapping(facts.get("regime"))
    return bool(
        facts.get("structural_break")
        or regime.get("persistent_one_way_decline")
        or (_number(regime.get("return20")) <= -.08 and facts.get("parent_trend") != "up")
    )


def _facts(entity: Mapping[str, object] | None) -> Mapping[str, object]:
    return _mapping(entity.get("mean_reversion")) if entity else {}


def _period(values: Mapping[str, object], period: int) -> float | None:
    return _optional_number(values.get(str(period), values.get(period)))


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _optional_number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _number(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0
