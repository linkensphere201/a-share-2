"""Board liquidity-capacity classification and market-fit ranking."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from statistics import median

from stock_harness.board_hotspot_evaluation import canonical_board_name


BOARD_CAPACITY_VERSION = "board-capacity-v1-turnover-percentiles"


def classify_board_capacities(
    snapshots: Mapping[str, Mapping[str, object]],
    names: Mapping[str, str],
    theme_profiles: Mapping[str, Mapping[str, object]] | None = None,
) -> dict[str, dict[str, object]]:
    """Assign one capacity profile to alias-equivalent board symbols."""
    grouped: dict[str, list[tuple[str, Mapping[str, object]]]] = {}
    for symbol, snapshot in snapshots.items():
        profile = (theme_profiles or {}).get(symbol, {})
        cluster = str(
            profile.get("theme_id")
            or canonical_board_name(names.get(symbol, symbol))
        )
        grouped.setdefault(cluster, []).append((symbol, snapshot))
    cluster_capacity = {
        cluster: _median_numeric(item.get("turnover_capacity_20") for _, item in rows)
        for cluster, rows in grouped.items()
    }
    universe = sorted(value for value in cluster_capacity.values()
                      if value is not None and value > 0)
    boundaries = {
        "p10": _percentile(universe, .10),
        "p50": _percentile(universe, .50),
        "p80": _percentile(universe, .80),
        "p95": _percentile(universe, .95),
    }
    result: dict[str, dict[str, object]] = {}
    for cluster, rows in grouped.items():
        capacity = cluster_capacity[cluster]
        profile = {
            "version": BOARD_CAPACITY_VERSION,
            "canonical_theme": cluster,
            "theme_name": next((
                str((theme_profiles or {}).get(symbol, {}).get("theme_name"))
                for symbol, _ in rows
                if (theme_profiles or {}).get(symbol, {}).get("theme_name")
            ), cluster),
            "capacity_tier": _capacity_tier(capacity, boundaries),
            "turnover_capacity_20": capacity,
            "turnover_recent_5": _median_numeric(
                item.get("turnover_recent_5") for _, item in rows
            ),
            "turnover_intensity": _median_numeric(
                item.get("turnover_intensity") for _, item in rows
            ),
            "member_count": max((_integer(item.get("member_count")) for _, item in rows),
                                default=0),
            "coverage_ratio": _median_numeric(
                item.get("coverage_ratio") for _, item in rows
            ),
            "turnover_concentration_hhi": _median_numeric(
                item.get("turnover_concentration_hhi") for _, item in rows
            ),
            "largest_member_share": _median_numeric(
                item.get("largest_member_share") for _, item in rows
            ),
            "alias_symbol_count": len(rows),
            "percentile_boundaries": boundaries,
        }
        for symbol, _ in rows:
            result[symbol] = dict(profile)
    return result


def market_capacity_fit(
    board: Mapping[str, object], market: Mapping[str, object],
) -> dict[str, object]:
    tier = str(board.get("capacity_tier") or "unknown")
    market_tier = str(market.get("capacity_tier") or "unknown")
    intensity = _number(board.get("turnover_intensity"))
    coverage = _number(board.get("coverage_ratio"))
    concentration = _number(board.get("turnover_concentration_hhi"))
    member_count = _integer(board.get("member_count"))
    market_compatible = _compatible(tier, market_tier)
    compatible = tier not in {"micro", "unknown"}
    reasons: list[str] = []
    if tier in {"micro", "unknown"}:
        reasons.append("capacity-too-small-or-unknown")
    if market_tier == "low" and tier in {"large", "mega"}:
        reasons.append("board-exceeds-low-market-capacity")
    if coverage is None or coverage < .65:
        compatible = False
        reasons.append("insufficient-capacity-coverage")
    if member_count < 8:
        compatible = False
        reasons.append("insufficient-board-breadth")
    if concentration is not None and concentration >= .45:
        compatible = False
        reasons.append("capacity-concentrated-in-one-member")
    base = {
        "low": {"micro": -8, "small": 6, "medium": 3, "large": -5, "mega": -9},
        "medium": {"micro": -8, "small": 2, "medium": 5, "large": 2, "mega": -2},
        "high": {"micro": -8, "small": 0, "medium": 2, "large": 6, "mega": 4},
    }.get(market_tier, {}).get(tier, -10)
    intensity_points = 0.0 if intensity is None else max(
        -5.0, min(7.0, (intensity - 1.0) * 18.0)
    )
    concentration_penalty = 0.0 if concentration is None else max(
        0.0, (concentration - .18) * 35.0
    )
    fit_score = float(base) + intensity_points - concentration_penalty
    return {
        "compatible": compatible,
        "market_compatible": market_compatible,
        "fit_score": round(fit_score, 2),
        "reasons": reasons,
        "market_capacity_tier": market_tier,
        "board_capacity_tier": tier,
    }


def _compatible(board_tier: str, market_tier: str) -> bool:
    if market_tier == "low":
        return board_tier in {"small", "medium"}
    if market_tier == "medium":
        return board_tier in {"small", "medium", "large"}
    if market_tier == "high":
        return board_tier in {"small", "medium", "large", "mega"}
    return False


def _capacity_tier(
    value: float | None, boundaries: Mapping[str, float | None],
) -> str:
    if value is None or value <= 0 or boundaries["p10"] is None:
        return "unknown"
    if value < float(boundaries["p10"]):
        return "micro"
    if value < float(boundaries["p50"]):
        return "small"
    if value < float(boundaries["p80"]):
        return "medium"
    if value < float(boundaries["p95"]):
        return "large"
    return "mega"


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    position = (len(values) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] * (1 - fraction) + values[upper] * fraction


def _median_numeric(values: Iterable[object]) -> float | None:
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    return median(numbers) if numbers else None


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _integer(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) else 0
