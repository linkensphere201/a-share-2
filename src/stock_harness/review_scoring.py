"""Versioned, deterministic scoring over immutable signal-review evidence."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from typing import Protocol


TREND_BREAKOUT_SCORER = "trend-breakout"
TREND_BREAKOUT_VERSION = "trend-breakout-score-v1"
MARKET_REGIME_SCORER = "market-regime"
MARKET_REGIME_VERSION = "market-regime-score-v1"
RECOGNITION_SCORER = "recognition"
RECOGNITION_VERSION = "recognition-score-v1"


class ReviewScorer(Protocol):
    system_id: str
    version: str
    entity_scope: str
    cadence: str
    ranking_universe: str
    input_dependencies: tuple[str, ...]
    hard_gates: tuple[str, ...]
    dimensions: tuple[str, ...]
    penalties: tuple[str, ...]
    tie_breakers: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    presentation_fields: tuple[str, ...]

    def score(self, entity: Mapping[str, object]) -> dict[str, object]: ...


class ReviewScorerRegistry:
    def __init__(self) -> None:
        self._scorers: dict[str, ReviewScorer] = {}

    def register(self, scorer: ReviewScorer) -> None:
        if scorer.system_id in self._scorers:
            raise ValueError(f"duplicate review scorer: {scorer.system_id}")
        self._scorers[scorer.system_id] = scorer

    def get(self, system_id: str) -> ReviewScorer:
        try:
            return self._scorers[system_id]
        except KeyError as error:
            raise ValueError(f"unknown review scorer: {system_id}") from error

    def definitions(self) -> list[dict[str, object]]:
        return [{
            "system_id": scorer.system_id,
            "scorer_version": scorer.version,
            "entity_scope": scorer.entity_scope,
            "cadence": scorer.cadence,
            "ranking_universe": scorer.ranking_universe,
            "input_dependencies": list(scorer.input_dependencies),
            "hard_gates": list(scorer.hard_gates),
            "dimensions": list(scorer.dimensions),
            "penalties": list(scorer.penalties),
            "tie_breakers": list(scorer.tie_breakers),
            "evidence_requirements": list(scorer.evidence_requirements),
            "presentation_fields": list(scorer.presentation_fields),
        } for scorer in self._scorers.values()]


@dataclass(frozen=True)
class _Scorer:
    system_id: str
    version: str
    entity_scope: str
    calculator: Callable[[Mapping[str, object]], dict[str, object]]
    cadence: str
    ranking_universe: str
    input_dependencies: tuple[str, ...]
    hard_gates: tuple[str, ...]
    dimensions: tuple[str, ...]
    penalties: tuple[str, ...]
    tie_breakers: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    presentation_fields: tuple[str, ...] = (
        "total_score", "grade", "verdict", "summary", "risk_summary",
        "change_summary",
    )

    def score(self, entity: Mapping[str, object]) -> dict[str, object]:
        result = self.calculator(entity)
        return {
            "system_id": self.system_id,
            "scorer_version": self.version,
            "entity_scope": self.entity_scope,
            **result,
        }


def default_scorer_registry() -> ReviewScorerRegistry:
    registry = ReviewScorerRegistry()
    registry.register(_Scorer(
        TREND_BREAKOUT_SCORER, TREND_BREAKOUT_VERSION, "board",
        _trend_breakout_score, "daily", "all-normalized-business-boards",
        ("daily-observation", "structural-trade-scenario"),
        ("complete-data", "valid-long-ordering", "stressed-rr-at-least-3"),
        ("risk_reward", "shape_trigger", "price_volume", "multi_horizon",
         "target_quality", "relative_strength"),
        ("entry-extension", "missing-volume-confirmation", "horizon-conflict"),
        ("eligible", "total_score", "stressed_rr", "shape", "price_volume", "symbol"),
        ("scenario", "target", "shape", "price-volume"),
    ))
    registry.register(_Scorer(
        MARKET_REGIME_SCORER, MARKET_REGIME_VERSION, "market",
        _market_regime_score, "daily", "shanghai-composite-and-active-market-value",
        ("market-daily-observation", "market-emotion"), (),
        ("shape_trend", "price_volume", "emotion", "breadth"), (),
        ("total_score", "symbol"), ("market-observation",),
    ))
    registry.register(_Scorer(
        RECOGNITION_SCORER, RECOGNITION_VERSION, "recognition-result",
        _recognition_score, "weekly", "active-recognition-results",
        ("recognition-result", "board-memberships"), (),
        ("recognition_baseline",), (), ("total_score", "symbol"),
        ("board-recognition-ranking",),
    ))
    return registry


@dataclass(frozen=True)
class ScorerExecution:
    results: list[dict[str, object]]
    error: str | None = None


def execute_scorer(
    scorer: ReviewScorer,
    entities: Sequence[Mapping[str, object]],
    *,
    prior_by_symbol: Mapping[str, Mapping[str, object]] | None = None,
    recent_by_symbol: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
) -> ScorerExecution:
    try:
        return ScorerExecution(score_entities(
            scorer, entities, prior_by_symbol=prior_by_symbol,
            recent_by_symbol=recent_by_symbol,
        ))
    except Exception as error:
        return ScorerExecution([], f"{type(error).__name__}: {error}")


def score_entities(
    scorer: ReviewScorer,
    entities: Sequence[Mapping[str, object]],
    *,
    prior_by_symbol: Mapping[str, Mapping[str, object]] | None = None,
    recent_by_symbol: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
) -> list[dict[str, object]]:
    prior_by_symbol = prior_by_symbol or {}
    recent_by_symbol = recent_by_symbol or {}
    universe = sorted(str(entity.get("item_key") or entity["symbol"]) for entity in entities)
    universe_digest = hashlib.sha256(json.dumps(
        universe, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    results = []
    for entity in entities:
        result = {
            **scorer.score(entity),
            "system_id": scorer.system_id,
            "scorer_version": scorer.version,
            "entity_scope": scorer.entity_scope,
        }
        result["entity_key"] = str(entity.get("item_key") or entity["symbol"])
        results.append(result)
    results.sort(key=_ranking_key)
    eligible_count = sum(bool(result["eligible"]) for result in results)
    for rank, result in enumerate(results, 1):
        result["rank"] = rank
        result["participant_count"] = len(results)
        result["eligible_count"] = eligible_count
        result["ranking_universe_digest"] = universe_digest
    for result in results:
        symbol = str(result["symbol"])
        history_key = str(result["entity_key"])
        prior = prior_by_symbol.get(history_key) or prior_by_symbol.get(symbol)
        recent = list(
            recent_by_symbol.get(history_key, recent_by_symbol.get(symbol, ()))
        )[:5]
        result["hard_events"] = _transition_hard_events(
            _sequence(result.get("hard_events")), prior,
        )
        result["comparison"] = _comparison(result, prior, recent, universe_digest)
        result["change_summary"] = _change_summary(result, prior, universe_digest)
        result["history"] = [_history_point(value) for value in recent]
    return results


def _ranking_key(value: Mapping[str, object]) -> tuple[object, ...]:
    components = _mapping(value.get("components"))
    return (
        not bool(value.get("eligible")),
        -_number(value.get("total_score"), 0),
        -_number(value.get("stressed_risk_reward"), -1),
        -_number(components.get("shape_trigger"), 0),
        -_number(components.get("price_volume"), 0),
        str(value.get("symbol", "")),
    )


def _trend_breakout_score(entity: Mapping[str, object]) -> dict[str, object]:
    symbol = str(entity["symbol"]).upper()
    metrics = _mapping(entity.get("metrics"))
    price_space = _mapping(metrics.get("price_space"))
    state = str(price_space.get("state") or "no-entry")
    targets = [
        _mapping(value) for value in _sequence(price_space.get("targets"))
        if isinstance(value, Mapping)
    ]
    selected = next((target for target in targets if (
        _optional_number(target.get("stressed_risk_reward_ratio")) is not None
        and _number(target.get("stressed_risk_reward_ratio"), 0) >= 3.0
    )), None)
    stressed_rr = (
        _optional_number(selected.get("stressed_risk_reward_ratio"))
        if selected else None
    )
    raw_rr = _optional_number(selected.get("risk_reward_ratio")) if selected else None
    hard_events = hard_signal_events(entity)
    disqualifiers = [str(value) for value in _sequence(entity.get("disqualifiers"))]
    if entity.get("coverage_state") != "complete":
        disqualifiers.append("incomplete-current-date-data")
    if state in {"invalidated", "no-entry"}:
        disqualifiers.append(f"scenario-{state}")
    if state == "extended":
        disqualifiers.append("entry-is-extended")
    if selected is None:
        disqualifiers.append("no-credible-target-at-3r")
    entry = _optional_number(price_space.get("entry_price"))
    invalidation = _optional_number(price_space.get("invalidation_price"))
    target_price = _optional_number(selected.get("price")) if selected else None
    if entry is None or invalidation is None or target_price is None or not (
        target_price > entry > invalidation
    ):
        disqualifiers.append("invalid-long-price-ordering")

    rr_points = _risk_reward_points(stressed_rr)
    shape_points = {
        "retest": 20.0, "triggered": 17.0, "waiting-trigger": 11.0,
        "extended": 5.0, "invalidated": 0.0, "no-entry": 0.0,
    }.get(state, 6.0)
    volume_ratio = _optional_number(metrics.get("volume_ratio20"))
    if state == "retest":
        volume_points = 15.0 if volume_ratio is not None and volume_ratio <= .9 else 10.0
    elif state == "triggered":
        volume_points = 15.0 if volume_ratio is not None and volume_ratio >= 1.2 else 9.0
    else:
        volume_points = 8.0 if volume_ratio is not None else 4.0
    short_state = str(_mapping(metrics.get("short_shape")).get("state") or "insufficient")
    medium_state = str(_mapping(metrics.get("medium_shape")).get("state") or "insufficient")
    alignment_points = sum({"rising": 5.0, "sideways": 3.0, "falling": 0.0}.get(
        value, 1.0,
    ) for value in (short_state, medium_state))
    evidence_ids = [str(value) for value in _sequence(selected.get("evidence_item_ids"))] if selected else []
    basis = str(selected.get("basis") or "") if selected else ""
    target_points = min(10.0, 3.0 + len(set(evidence_ids)) * 2.0 + (
        2.0 if "+" in basis else 0.0
    )) if selected else 0.0
    relative = _mapping(metrics.get("relative_strength"))
    rs5 = _optional_number(relative.get("5"))
    rs20 = _optional_number(relative.get("20"))
    relative_points = sum(
        2.5 if value is not None and value >= .02 else
        1.25 if value is not None and value >= 0 else 0.0
        for value in (rs5, rs20)
    )
    components = {
        "risk_reward": round(rr_points, 2),
        "shape_trigger": round(shape_points, 2),
        "price_volume": round(volume_points, 2),
        "multi_horizon": round(alignment_points, 2),
        "target_quality": round(target_points, 2),
        "relative_strength": round(relative_points, 2),
    }
    penalties = []
    if state == "extended":
        penalties.append({"code": "entry-extension", "points": 15.0})
    if state == "triggered" and (volume_ratio is None or volume_ratio < 1.0):
        penalties.append({"code": "missing-volume-confirmation", "points": 6.0})
    if short_state == "falling" or medium_state == "falling":
        penalties.append({"code": "horizon-conflict", "points": 6.0})
    total = max(0.0, min(100.0, sum(components.values()) - sum(
        _number(value["points"], 0) for value in penalties
    )))
    eligible = not disqualifiers
    verdict = (
        "可关注趋势突破机会" if eligible and total >= 75 else
        "具备趋势突破交易空间" if eligible else
        "有异动但暂不具备交易空间"
    )
    positive = _trend_positive_reasons(state, stressed_rr, volume_ratio, short_state, medium_state)
    risk = _trend_risk_summary(disqualifiers, state, basis)
    return {
        "symbol": symbol, "eligible": eligible,
        "total_score": round(total, 2), "grade": score_grade(total),
        "verdict": verdict,
        "summary": "；".join(positive[:2]) + "。",
        "risk_summary": risk,
        "components": components, "penalties": penalties,
        "disqualifiers": sorted(set(disqualifiers)),
        "hard_events": hard_events,
        "selected_scenario_id": price_space.get("scenario_item_id"),
        "selected_target_label": selected.get("label") if selected else None,
        "selected_target_price": target_price,
        "raw_risk_reward": raw_rr,
        "stressed_risk_reward": stressed_rr,
        "evidence_refs": evidence_ids,
    }


def _market_regime_score(entity: Mapping[str, object]) -> dict[str, object]:
    symbol = str(entity["symbol"]).upper()
    payload = _mapping(entity.get("payload"))
    metrics = _mapping(payload.get("metrics"))
    states = {str(value) for value in _sequence(payload.get("state_codes"))}
    short_state = str(_mapping(metrics.get("short_shape")).get("state") or "insufficient")
    medium_state = str(_mapping(metrics.get("medium_shape")).get("state") or "insufficient")
    shape = sum({"rising": 12.5, "sideways": 7.0, "falling": 2.0}.get(
        value, 3.0,
    ) for value in (short_state, medium_state))
    returns = _mapping(metrics.get("returns"))
    return5 = _optional_number(returns.get("5"))
    volume_ratio = _optional_number(metrics.get("volume_ratio20"))
    price_volume = 10.0
    if return5 is not None:
        price_volume += 5.0 if return5 > .01 else 2.0 if return5 >= 0 else -3.0
    if volume_ratio is not None:
        price_volume += 5.0 if .8 <= volume_ratio <= 1.8 else 2.0
    emotion_payload = _mapping(payload.get("emotion"))
    emotion_metrics = _mapping(emotion_payload.get("metrics"))
    breadth = _optional_number(emotion_metrics.get("breadth"))
    sealing = _optional_number(emotion_metrics.get("sealing_rate"))
    emotion = 12.5
    if breadth is not None:
        emotion += max(-6.0, min(6.0, breadth * 8.0))
    if sealing is not None:
        emotion += max(-4.0, min(6.5, (sealing - .5) * 13.0))
    diagnostics = _mapping(metrics.get("active_market_value_diagnostics"))
    coverage = _optional_number(diagnostics.get("coverage_ratio"))
    breadth_active = 10.0 + (10.0 * coverage if coverage is not None else 0.0)
    comparison = _mapping(payload.get("comparison"))
    recent = 5.0
    if comparison.get("transition") == "strengthened":
        recent = 10.0
    elif comparison.get("transition") in {"weakened", "invalidated"}:
        recent = 2.0
    components = {
        "shape_trend": round(shape, 2),
        "price_volume": round(max(0.0, min(20.0, price_volume)), 2),
        "emotion": round(max(0.0, min(25.0, emotion)), 2),
        "breadth_active_value": round(max(0.0, min(20.0, breadth_active)), 2),
        "recent_change": recent,
    }
    total = max(0.0, min(100.0, sum(components.values())))
    verdict = (
        "积极进攻" if total >= 85 else "谨慎进攻" if total >= 75 else
        "轮动分化" if total >= 60 else "防守观察" if total >= 45 else "风险释放"
    )
    hard_events = hard_signal_events({
        "state_codes": list(states), "attention_reasons": [],
    })
    return {
        "symbol": symbol, "eligible": True,
        "total_score": round(total, 2), "grade": score_grade(total),
        "verdict": verdict,
        "summary": f"短周期{_shape_text(short_state)}，中周期{_shape_text(medium_state)}。",
        "risk_summary": "市场环境分只用于环境判断，不与板块机会混排。",
        "components": components, "penalties": [], "disqualifiers": [],
        "hard_events": hard_events, "evidence_refs": [],
        "selected_scenario_id": None, "selected_target_label": None,
        "selected_target_price": None, "raw_risk_reward": None,
        "stressed_risk_reward": None,
    }


def _recognition_score(entity: Mapping[str, object]) -> dict[str, object]:
    symbol = str(entity["symbol"]).upper()
    base = max(0.0, min(1.0, _number(entity.get("score"), 0)))
    total = round(base * 100, 2)
    payload = _mapping(entity.get("payload"))
    board_count = int(_number(payload.get("board_count"), 0))
    profile = str(entity.get("profile") or "recognition")
    verdict = "高辨识度" if total >= 85 else "重点辨识度观察" if total >= 75 else "辨识度候选"
    return {
        "symbol": symbol, "eligible": bool(entity.get("active", True)),
        "total_score": total, "grade": score_grade(total), "verdict": verdict,
        "summary": f"{profile}结果覆盖{board_count}个板块，沿用已验证的辨识度基础分。",
        "risk_summary": "辨识度不等于趋势入场机会，需与其他体系独立判断。",
        "components": {"recognition_baseline": total}, "penalties": [],
        "disqualifiers": [], "hard_events": [], "evidence_refs": [],
        "selected_scenario_id": None, "selected_target_label": None,
        "selected_target_price": None, "raw_risk_reward": None,
        "stressed_risk_reward": None,
    }


def hard_signal_events(entity: Mapping[str, object]) -> list[dict[str, object]]:
    states = {str(value) for value in _sequence(entity.get("state_codes"))}
    states.update(str(value) for value in _sequence(entity.get("attention_reasons")))
    definitions = {
        "1y-descending-envelope-broken": ("major-trend-breakout", "up", "high"),
        "6m-descending-envelope-broken": ("major-trend-breakout", "up", "high"),
        "3m-descending-envelope-broken": ("trend-breakout", "up", "medium"),
        "descending-envelope-1y-broken": ("major-trend-breakout", "up", "high"),
        "descending-envelope-6m-broken": ("major-trend-breakout", "up", "high"),
        "descending-envelope-3m-broken": ("trend-breakout", "up", "medium"),
        "3m-descending-envelope-approaching": ("trend-boundary-proximity", "neutral", "medium"),
        "bullish-boundary-proximity": ("trend-boundary-proximity", "neutral", "medium"),
        "bullish-boundary-triggered": ("bullish-boundary-triggered", "up", "high"),
        "oversold-rebound-triggered": ("oversold-rebound-triggered", "up", "high"),
        "downside-exhaustion": ("downside-exhaustion", "neutral", "medium"),
        "oversold-exhaustion-candidate": ("downside-exhaustion", "neutral", "medium"),
        "sudden-volume-expansion": ("sudden-volume-expansion", "neutral", "medium"),
        "boundary-volume-contraction": ("boundary-volume-contraction", "neutral", "medium"),
        "relative-strength-regime": ("relative-strength-regime", "neutral", "medium"),
        "prior-state-strengthened": ("prior-state-strengthened", "up", "medium"),
        "prior-state-weakened": ("prior-state-weakened", "down", "medium"),
        "prior-state-changed": ("prior-state-changed", "neutral", "medium"),
        "prior-state-invalidated": ("structure-invalidated", "down", "high"),
    }
    result = []
    seen = set()
    for source in sorted(states):
        definition = definitions.get(source)
        if definition is None or definition[0] in seen:
            continue
        seen.add(definition[0])
        result.append({
            "event_type": definition[0], "direction": definition[1],
            "severity": definition[2], "state": "new", "source_code": source,
        })
    return result


def _transition_hard_events(
    current_events: Sequence[object], prior: Mapping[str, object] | None,
) -> list[dict[str, object]]:
    current = {
        str(event.get("event_type")): dict(event)
        for event in current_events if isinstance(event, Mapping)
    }
    prior_events = {
        str(event.get("event_type")): dict(event)
        for event in _sequence(prior.get("hard_events") if prior else None)
        if isinstance(event, Mapping)
        and str(event.get("state") or "") != "resolved"
    }
    result = []
    for event_type, event in current.items():
        event["state"] = "continuing" if event_type in prior_events else "new"
        result.append(event)
    for event_type, event in prior_events.items():
        if event_type in current:
            continue
        event["state"] = "resolved"
        result.append(event)
    return sorted(result, key=lambda event: (
        event.get("state") == "resolved", str(event.get("event_type", "")),
    ))


def score_grade(score: float) -> str:
    if score >= 85:
        return "S"
    if score >= 75:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"


def _risk_reward_points(value: float | None) -> float:
    if value is None or value < 3:
        return 0.0
    points = ((3.0, 24.0), (4.0, 30.0), (5.0, 35.0), (6.0, 38.0), (8.0, 40.0))
    for index in range(1, len(points)):
        left, right = points[index - 1], points[index]
        if value <= right[0]:
            return left[1] + (value - left[0]) / (right[0] - left[0]) * (right[1] - left[1])
    return 40.0


def _trend_positive_reasons(
    state: str, rr: float | None, volume_ratio: float | None,
    short_state: str, medium_state: str,
) -> list[str]:
    result = []
    if rr is not None:
        result.append(f"压力调整后盈亏比{rr:.2f}:1")
    result.append({
        "retest": "突破后进入回踩确认", "triggered": "结构边界已经触发",
        "waiting-trigger": "结构接近触发边界",
    }.get(state, "当前结构尚未形成标准入场"))
    if volume_ratio is not None:
        result.append(f"当日量为20日中位量的{volume_ratio:.2f}倍")
    if short_state == "rising" and medium_state == "rising":
        result.append("中短周期方向一致")
    return result


def _trend_risk_summary(disqualifiers: Sequence[str], state: str, basis: str) -> str:
    if "no-credible-target-at-3r" in disqualifiers:
        return "最近可信目标不足3:1，不进入趋势机会榜。"
    if state == "extended":
        return "价格已经偏离合理入场区，保留异动但不追高。"
    if state == "invalidated":
        return "原结构已经失效，不进入多头机会榜。"
    if "estimated-volume-at-price" in basis:
        return "目标包含日线估算成交密集区，需结合上方压力复核。"
    return "继续观察确认位与失效位，评分不构成交易指令。"


def _comparison(
    current: Mapping[str, object], prior: Mapping[str, object] | None,
    recent: Sequence[Mapping[str, object]], universe_digest: str,
) -> dict[str, object]:
    if prior is None:
        return {"state": "new", "score_delta": None, "rank_delta": None,
                "universe_changed": False, "recent_count": len(recent)}
    prior_score = _number(prior.get("total_score"), 0)
    prior_rank = int(_number(prior.get("rank"), 0))
    current_score = _number(current.get("total_score"), 0)
    current_rank = int(_number(current.get("rank"), 0))
    delta = round(current_score - prior_score, 2)
    return {
        "state": "strengthened" if delta >= 2 else "weakened" if delta <= -2 else "unchanged",
        "score_delta": delta,
        "rank_delta": prior_rank - current_rank if prior_rank else None,
        "universe_changed": prior.get("ranking_universe_digest") != universe_digest,
        "recent_count": len(recent),
    }


def _change_summary(
    current: Mapping[str, object], prior: Mapping[str, object] | None,
    universe_digest: str,
) -> str:
    if prior is None:
        return "本轮建立首个兼容评分基线。"
    delta = _number(current.get("total_score"), 0) - _number(prior.get("total_score"), 0)
    rank_delta = int(_number(prior.get("rank"), 0) - _number(current.get("rank"), 0))
    universe = "；排名池发生变化" if prior.get("ranking_universe_digest") != universe_digest else ""
    return f"较上一轮{delta:+.1f}分，排名变化{rank_delta:+d}{universe}。"


def _history_point(value: Mapping[str, object]) -> dict[str, object]:
    effective_date = value.get("effective_date")
    return {
        "effective_date": (
            effective_date.isoformat() if hasattr(effective_date, "isoformat")
            else effective_date
        ),
        "total_score": value.get("total_score"), "grade": value.get("grade"),
        "rank": value.get("rank"), "eligible": value.get("eligible"),
    }


def _shape_text(value: str) -> str:
    return {"rising": "上行", "sideways": "震荡", "falling": "下行"}.get(value, "数据不足")


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _optional_number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _number(value: object, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) else default
