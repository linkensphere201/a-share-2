"""Unified discovery, derived from dated features rather than review-run cadence."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from stock_harness.board_hotspot_evaluation import canonical_board_name

VERSION = "board-hotspot-emergence-v8-session-discovery"


def _number(value: object, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) else default


def _map(value: object) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def sample(feature: Mapping) -> dict:
    metrics = _map(feature.get("metrics"))
    members = _map(feature.get("member_snapshot"))
    returns = _map(metrics.get("returns"))
    relative = _map(metrics.get("relative_strength"))
    shape = _map(metrics.get("hotspot_shape"))
    member_count = _number(members.get("member_count"))
    coverage = min(_number(members.get("coverage_ratio")),
                   _number(members.get("return_5_covered_count")) / member_count
                   if member_count else 0.0)
    valid = feature.get("coverage_state") == "complete" and coverage >= .65 and member_count >= 3
    price = _number(returns.get("5"))
    rs = _number(relative.get("5"))
    volume = _number(metrics.get("recent_volume_ratio_5_5"))
    breadth = _number(members.get("positive_return_5_ratio"))
    leaders = list(members.get("trend_leader_symbols") or [])
    support = valid and price > 0 and rs >= .015 and volume >= 1.0
    return {
        "effective_date": feature.get("effective_date"), "valid": valid,
        "return_1": _number(returns.get("1")), "return_5": price,
        "relative_strength_5": rs, "volume_persistence_5": volume,
        "positive_return_5_ratio": breadth, "member_coverage_ratio": coverage,
        "leader_symbols": leaders, "supported": support,
        "price_supported": valid and price > 0 and rs >= .015,
        "diffusing": support and price >= .02 and breadth >= .55,
        "shape_path": shape.get("path", "none"),
        "extension_atr": _number(shape.get("extension_from_ma20_atr")),
        "limit_up_count": int(_number(members.get("limit_up_count"))),
        "max_limit_up_streak": int(_number(members.get("max_limit_up_streak"))),
        "membership_semantics": members.get("membership_semantics", "unknown"),
    }


class UnifiedHotspotScorer:
    system_id = "board-hotspot-emergence"
    version = VERSION
    entity_scope = "board"

    def score(self, entity: Mapping) -> dict:
        features = entity.get("session_features") or []
        samples = [sample(item) for item in features][-5:]
        current = samples[-1] if samples else sample({})
        recent = samples[-3:]
        counts = Counter(symbol for item in recent if item["valid"]
                         for symbol in set(item["leader_symbols"]))
        persistent_leaders = sorted(symbol for symbol in current["leader_symbols"]
                                    if counts[symbol] >= 2)
        supported = sum(item["supported"] for item in recent)
        price_supported = sum(item["price_supported"] for item in recent)
        broad = sum(item["diffusing"] for item in recent)
        core = bool(persistent_leaders) and current["positive_return_5_ratio"] >= .35
        diffusion = broad >= 2 and current["diffusing"]
        reasons = []
        dates = [str(item["effective_date"] or "") for item in samples]
        if (len(samples) < 5 or not all(item["valid"] for item in samples)
                or not all(dates) or dates != sorted(set(dates))):
            reasons.append("incomplete-session-window")
        if not current["supported"] and not (core and current["price_supported"]):
            reasons.append("weak-current-price-volume")
        if supported < 2 and not (core and price_supported >= 2):
            reasons.append("insufficient-session-persistence")
        if not core and not diffusion:
            reasons.append("no-persistent-leader-or-diffusion")
        stage = "failed"
        if not reasons:
            stage = "breadth-expanding" if diffusion else "leader-ignited"
            if (diffusion and current["positive_return_5_ratio"] >= .65
                    and current["return_5"] >= .04
                    and current["shape_path"] != "none"):
                stage = "hotspot-confirmed"
        warnings = []
        if core and not diffusion:
            warnings.append("core-led-without-broad-diffusion")
        if current["extension_atr"] >= 3 or current["return_5"] >= .14:
            warnings.append("extended-price-risk")
        if current["return_1"] <= -.035:
            reasons.append("current-sharp-reversal")
            stage = "diverging" if supported >= 2 else "failed"
        components = {
            "relative_strength": min(25., max(0., current["relative_strength_5"]) * 300),
            "participation": current["positive_return_5_ratio"] * 25,
            "persistence": (price_supported if core else supported) * 8.,
            "leader": 15. if persistent_leaders else 0.,
            "activity": min(11., max(0., current["volume_persistence_5"] - .8) * 20),
        }
        score = round(sum(components.values()), 2)
        labels = {"leader-ignited": "核心领涨", "breadth-expanding": "扩散中",
                  "hotspot-confirmed": "热点确认", "diverging": "分歧转弱", "failed": "证据不足"}
        return {
            "symbol": entity["symbol"], "eligible": not reasons,
            "total_score": score, "raw_score": score,
            "grade": "B" if not reasons else "D", "verdict": labels[stage],
            "summary": f"{labels[stage]}；近5日{current['return_5']:.1%}，"
                       f"相对强度{current['relative_strength_5']:+.1%}，"
                       f"5日上涨成员占比{current['positive_return_5_ratio']:.0%}。",
            "risk_summary": "; ".join(warnings), "penalties": [],
            "disqualifiers": reasons, "hard_events": [], "components": components,
            "hotspot_stage": stage, "setup_path": current["shape_path"],
            "candidate_streak": price_supported if core else supported,
            "leader_symbols": persistent_leaders,
            "hotspot_window_version": "actual-trading-sessions-v2",
            "hotspot_window_state": "persistent" if not reasons else "fragmented",
            "hotspot_window_eligible": not reasons,
            "hotspot_window_observed_sessions": sum(item["valid"] for item in samples),
            "hotspot_window_qualified_sessions": sum(
                item["price_supported"] if core else item["supported"] for item in samples
            ),
            "hotspot_window_shape_support_sessions": sum(item["shape_path"] != "none" for item in samples),
            "hotspot_window_failure_reasons": reasons,
            "hotspot_session_samples": samples,
            "hotspot_window_dates": [item["effective_date"] for item in samples],
            "member_coverage_ratio": current["member_coverage_ratio"],
            "positive_return_5_ratio": current["positive_return_5_ratio"],
            "limit_up_count": current["limit_up_count"],
            "max_limit_up_streak": current["max_limit_up_streak"],
            "membership_semantics": current["membership_semantics"],
            "observation_only": True,
        }


def apply_discovery_visibility(results: Sequence[dict], names: Mapping,
                               themes: Mapping, market: Mapping, capacities: Mapping) -> None:
    representatives: dict[str, dict] = {}
    for result in results:
        symbol = result["symbol"]
        theme = _map(themes.get(symbol))
        capacity = _map(capacities.get(symbol))
        identity = str(theme.get("theme_id") or canonical_board_name(str(names.get(symbol, symbol))))
        reasons = list(result["disqualifiers"])
        if not theme.get("signal_eligible", True):
            reasons.append("non-business-theme")
        result.update({
            "canonical_theme": identity, "theme_name": theme.get("theme_name") or names.get(symbol),
            "theme_signal_eligible": theme.get("signal_eligible", True),
            "radar_visible": False, "radar_rank": None,
            "visibility_reasons": reasons,
            "market_liquidity_capacity": market.get("capacity_tier"),
            "market_liquidity_direction": market.get("direction"),
            "market_liquidity_regime": market.get("regime"),
            "board_capacity_concentration_hhi": capacity.get("turnover_concentration_hhi"),
        })
        if _number(capacity.get("turnover_concentration_hhi")) >= .45:
            result["risk_summary"] = "; ".join(filter(None, [result["risk_summary"], "concentrated-participation"]))
        if reasons and not (result.get("session_tracking") and result.get("risk_visible")
                            and theme.get("signal_eligible", True)):
            continue
        previous = representatives.get(identity)
        if previous is None or (-result["total_score"], symbol) < (-previous["total_score"], previous["symbol"]):
            representatives[identity] = result
    for rank, result in enumerate(sorted(representatives.values(), key=lambda r: (-r["total_score"], r["symbol"])), 1):
        result.update(radar_visible=True, radar_rank=rank)
    for result in results:
        if not result["visibility_reasons"] and not result["radar_visible"]:
            result["visibility_reasons"] = ["same-theme-representative"]


def collapse_overlapping_hotspots(results, memberships):
    """Conservative presentation grouping; never count one core as many themes."""
    representatives = []
    for row in sorted((r for r in results if r.get("radar_visible")),
                      key=lambda r: (-r["total_score"], r["symbol"])):
        members = {m["symbol"] for m in memberships.get(row["symbol"], [])}
        cores = set(row.get("leader_symbols") or [])
        match = None
        for prior, prior_members, prior_cores in representatives:
            union = members | prior_members
            if (union and len(members & prior_members) / len(union) >= .8
                    and cores and prior_cores and cores == prior_cores
                    and row.get("change_bucket") == prior.get("change_bucket")):
                match = prior
                break
        if match is None:
            representatives.append((row, members, cores))
        else:
            row.update(radar_visible=False, radar_rank=None,
                       related_representative=match["symbol"],
                       visibility_reasons=["overlapping-theme-representative"])
            match.setdefault("related_themes", []).append({"symbol": row["symbol"], "name": row.get("theme_name")})
    for rank, (row, _, _) in enumerate(representatives, 1):
        row["radar_rank"] = rank
