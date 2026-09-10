"""Built-in board observation-system plugins."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from stock_harness.observation_systems.contracts import (
    ObservationSystemContext,
    ObservationSystemExecution,
)
from stock_harness.review_scoring import (
    TREND_BREAKOUT_SCORER,
    execute_scorer,
    score_entities,
    score_grade,
)


BOARD_HOTSPOT_SYSTEM = "board-hotspot-emergence"
BOARD_HOTSPOT_VERSION = "board-hotspot-emergence-v1"


class TrendBreakoutSystem:
    system_id = TREND_BREAKOUT_SCORER
    entity_scope = "board"
    display_name = "趋势突破"
    dependencies: tuple[str, ...] = ()

    def __init__(self, version: str) -> None:
        self.version = version

    def definition(self) -> dict[str, object]:
        return {
            "system_id": self.system_id,
            "version": self.version,
            "entity_scope": self.entity_scope,
            "display_name": self.display_name,
            "dependencies": [],
            "score_combination": "independent",
        }

    def execute(self, context: ObservationSystemContext) -> ObservationSystemExecution:
        scorer = context.scorer_registry.get(self.system_id)
        execution = execute_scorer(
            scorer, context.observations,
            prior_by_symbol=context.prior_scores.get(self.system_id),
            recent_by_symbol=context.recent_scores.get(self.system_id),
        )
        return ObservationSystemExecution(
            self.system_id, self.version, self.entity_scope,
            execution.results, execution.error,
        )


class BoardHotspotSystem:
    system_id = BOARD_HOTSPOT_SYSTEM
    version = BOARD_HOTSPOT_VERSION
    entity_scope = "board"
    display_name = "近期热点"
    dependencies = ("board_hotspot_snapshots",)

    def definition(self) -> dict[str, object]:
        return {
            "system_id": self.system_id,
            "version": self.version,
            "entity_scope": self.entity_scope,
            "display_name": self.display_name,
            "dependencies": list(self.dependencies),
            "score_combination": "independent",
            "lifecycle": [
                "leader-ignited", "trend-emerging", "breadth-expanding",
                "hotspot-confirmed", "accelerating", "diverging",
                "exhausted", "failed",
            ],
        }

    def execute(self, context: ObservationSystemContext) -> ObservationSystemExecution:
        snapshots = context.dependencies["board_hotspot_snapshots"]
        if not isinstance(snapshots, Mapping):
            raise ValueError("board_hotspot_snapshots must be a mapping")
        prior = context.prior_scores.get(self.system_id, {})
        entities = []
        for observation in context.observations:
            symbol = str(observation["symbol"])
            entities.append({
                **observation,
                "hotspot_snapshot": snapshots.get(symbol, {}),
                "prior_hotspot": prior.get(symbol, {}),
            })
        scorer = _BoardHotspotScorer()
        results = score_entities(
            scorer, entities, prior_by_symbol=prior,
            recent_by_symbol=context.recent_scores.get(self.system_id),
        )
        return ObservationSystemExecution(
            self.system_id, self.version, self.entity_scope, results,
        )


class _BoardHotspotScorer:
    system_id = BOARD_HOTSPOT_SYSTEM
    version = BOARD_HOTSPOT_VERSION
    entity_scope = "board"

    def score(self, entity: Mapping[str, object]) -> dict[str, object]:
        metrics = _mapping(entity.get("metrics"))
        breadth = _mapping(metrics.get("board_breadth"))
        snapshot = _mapping(entity.get("hotspot_snapshot"))
        prior = _mapping(entity.get("prior_hotspot"))
        returns = _mapping(metrics.get("returns"))
        relative = _mapping(metrics.get("relative_strength"))

        return5 = _number(returns.get("5")) or 0.0
        return20 = _number(returns.get("20")) or 0.0
        rs5 = _number(relative.get("5"))
        rs20 = _number(relative.get("20"))
        volume = _number(metrics.get("volume_ratio20"))
        volume_persistence = _number(metrics.get("recent_volume_ratio_5_5"))
        breadth_value = _number(breadth.get("breadth")) or 0.0
        breadth5 = _number(snapshot.get("positive_return_5_ratio"))
        coverage = _number(snapshot.get("coverage_ratio"))
        concentration = _number(breadth.get("impact_concentration_hhi"))
        limit_up_count = int(_number(snapshot.get("limit_up_count")) or 0)
        broken_up_count = int(_number(snapshot.get("broken_up_count")) or 0)
        max_streak = int(_number(snapshot.get("max_limit_up_streak")) or 0)

        components = {
            "trend": _bounded_positive(return5, .08, 8)
                + _bounded_positive(return20, .18, 8)
                + _shape_points(metrics),
            "relative_strength": _bounded_positive(rs5, .06, 9)
                + _bounded_positive(rs20, .12, 11),
            "breadth": _bounded_signed(breadth_value, 14)
                + _bounded_positive(breadth5, .7, 6),
            "leader_echelon": min(25.0, limit_up_count * 3.5
                + max_streak * 5.0 + min(broken_up_count, 2)),
            "activity": _activity_points(volume, volume_persistence),
        }
        components = {key: round(value, 2) for key, value in components.items()}
        penalties: list[dict[str, object]] = []
        disqualifiers: list[str] = []
        if entity.get("coverage_state") != "complete":
            disqualifiers.append("incomplete-board-series")
        if coverage is None or coverage < .5:
            penalties.append({"code": "insufficient-member-coverage", "points": 18.0})
        if concentration is not None and concentration >= .45:
            penalties.append({"code": "single-member-concentration", "points": 15.0})
        elif concentration is not None and concentration >= .30:
            penalties.append({"code": "concentrated-participation", "points": 8.0})
        if volume is not None and volume >= 1.8 and return5 < .025 and breadth_value < .15:
            penalties.append({"code": "one-session-volume-noise", "points": 15.0})
        if volume is not None and volume >= 1.5 and return5 <= 0:
            penalties.append({"code": "volume-price-divergence", "points": 10.0})
        if return20 >= .28:
            penalties.append({"code": "late-stage-extension", "points": 10.0})

        raw_score = max(0.0, min(100.0, sum(components.values()) - sum(
            float(item["points"]) for item in penalties
        )))
        prior_score = _number(prior.get("total_score"))
        score = raw_score if prior_score is None else raw_score * .65 + prior_score * .35
        prior_stage = str(prior.get("hotspot_stage") or "")
        prior_streak = int(_number(prior.get("candidate_streak")) or 0)
        candidate = raw_score >= 48 and components["trend"] >= 8 and (
            components["leader_echelon"] >= 4 or components["breadth"] >= 10
        )
        candidate_streak = prior_streak + 1 if candidate else 0
        delta = score - prior_score if prior_score is not None else 0.0
        stage = _hotspot_stage(
            score, raw_score, delta, components, prior_stage, candidate_streak,
            breadth_value,
        )
        eligible = (
            not disqualifiers
            and stage in {"trend-emerging", "breadth-expanding", "hotspot-confirmed", "accelerating"}
            and score >= 55
            and (candidate_streak >= 2 or max_streak >= 2)
        )
        direction = "new" if prior_score is None else (
            "strengthening" if delta >= 2 else "declining" if delta <= -2 else "stable"
        )
        prior_peak = _number(prior.get("peak_score"))
        restarting = prior_stage in {"", "failed", "exhausted"} and stage not in {
            "failed", "exhausted",
        }
        peak = score if restarting or prior_peak is None else max(score, prior_peak)
        summary = _summary(stage, limit_up_count, max_streak, breadth_value, return5)
        risks = [str(item["code"]) for item in penalties]
        return {
            "symbol": str(entity["symbol"]).upper(),
            "eligible": eligible,
            "total_score": round(score, 2),
            "raw_score": round(raw_score, 2),
            "grade": score_grade(score),
            "verdict": _stage_label(stage),
            "summary": summary,
            "risk_summary": "；".join(risks[:3]) if risks else "暂无显著噪音项。",
            "components": components,
            "penalties": penalties,
            "disqualifiers": disqualifiers,
            "hard_events": [],
            "hotspot_stage": stage,
            "score_direction": direction,
            "score_delta": round(delta, 2),
            "peak_score": round(peak, 2),
            "drawdown_from_peak": round(peak - score, 2),
            "candidate_streak": candidate_streak,
            "limit_up_count": limit_up_count,
            "broken_up_count": broken_up_count,
            "max_limit_up_streak": max_streak,
            "member_coverage_ratio": coverage,
        }


def _hotspot_stage(
    score: float, raw_score: float, delta: float, components: Mapping[str, float],
    prior_stage: str, candidate_streak: int, breadth: float,
) -> str:
    active_prior = prior_stage in {
        "breadth-expanding", "hotspot-confirmed", "accelerating", "diverging",
    }
    if active_prior and candidate_streak == 0:
        return "exhausted" if raw_score < 35 or breadth < -.2 else "diverging"
    if active_prior and (raw_score < 35 or (delta <= -12 and breadth < 0)):
        return "exhausted"
    if active_prior and delta <= -4:
        return "diverging"
    if score >= 78 and delta >= 2 and candidate_streak >= 2:
        return "accelerating"
    if score >= 68 and candidate_streak >= 2:
        return "hotspot-confirmed"
    if score >= 58 and components["breadth"] >= 12 and candidate_streak >= 2:
        return "breadth-expanding"
    if score >= 52 and components["trend"] >= 10 and candidate_streak >= 2:
        return "trend-emerging"
    if components["leader_echelon"] >= 8 and score >= 40:
        return "leader-ignited"
    return "failed"


def _shape_points(metrics: Mapping[str, object]) -> float:
    points = 0.0
    for key in ("short_shape", "medium_shape"):
        state = str(_mapping(metrics.get(key)).get("state") or "")
        points += {"rising": 2.0, "sideways": 1.0}.get(state, 0.0)
    return points


def _activity_points(volume: float | None, persistence: float | None) -> float:
    current = 0.0 if volume is None else min(9.0, max(0.0, (volume - .8) / 1.2 * 9))
    sustained = 0.0 if persistence is None else min(6.0, max(0.0, (persistence - .9) / .6 * 6))
    return current + sustained


def _bounded_positive(value: float | None, ceiling: float, points: float) -> float:
    if value is None or value <= 0:
        return 0.0
    return min(points, value / ceiling * points)


def _bounded_signed(value: float | None, points: float) -> float:
    if value is None:
        return 0.0
    return min(points, max(0.0, (value + .1) / .8 * points))


def _summary(
    stage: str, limit_up_count: int, max_streak: int,
    breadth: float, return5: float,
) -> str:
    return (
        f"阶段：{_stage_label(stage)}；近5日涨幅{return5 * 100:.1f}%，"
        f"上涨广度{breadth * 100:.0f}%；涨停{limit_up_count}家，"
        f"最高连板{max_streak}板。"
    )


def _stage_label(stage: str) -> str:
    return {
        "leader-ignited": "龙头点火",
        "trend-emerging": "趋势形成",
        "breadth-expanding": "扩散增强",
        "hotspot-confirmed": "热点确认",
        "accelerating": "加速",
        "diverging": "分歧衰减",
        "exhausted": "退潮",
        "failed": "未形成热点",
    }.get(stage, stage)


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None
