"""Causal, independently ranked leading radar for board-hotspot formation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from stock_harness.board_capacity import market_capacity_fit
from stock_harness.board_hotspot_evaluation import canonical_board_name
from stock_harness.market_liquidity import stabilize_seat_budget
from stock_harness.observation_systems.contracts import (
    ObservationSystemContext,
    ObservationSystemExecution,
)
from stock_harness.observation_systems.board_hotspot_leading_policy import (
    LEADING_WINDOW_SESSIONS,
    build_leading_sample,
    leading_components,
    leading_dimensions,
    leading_penalties,
    leading_trajectory,
    structure_is_actionable,
)
from stock_harness.review_scoring import score_entities, score_grade


BOARD_HOTSPOT_LEADING_SYSTEM = "board-hotspot-leading"
BOARD_HOTSPOT_LEADING_VERSION = "board-hotspot-leading-v2-causal-trajectory"


class BoardHotspotLeadingSystem:
    system_id = BOARD_HOTSPOT_LEADING_SYSTEM
    version = BOARD_HOTSPOT_LEADING_VERSION
    entity_scope = "board"
    display_name = "热点前导"
    dependencies = (
        "board_hotspot_features", "board_names", "market_liquidity_context",
        "board_capacity_features", "board_theme_profiles",
    )

    def definition(self) -> dict[str, object]:
        return {
            "system_id": self.system_id,
            "version": self.version,
            "entity_scope": self.entity_scope,
            "display_name": self.display_name,
            "dependencies": list(self.dependencies),
            "score_combination": "independent",
            "lifecycle": [
                "watch", "strengthening", "launch-confirmed", "invalidated",
            ],
        }

    def execute(self, context: ObservationSystemContext) -> ObservationSystemExecution:
        features = context.dependencies["board_hotspot_features"]
        if not isinstance(features, Mapping):
            raise ValueError("board_hotspot_features must be a mapping")
        prior = context.prior_scores.get(self.system_id, {})
        recent = context.recent_scores.get(self.system_id, {})
        entities = []
        for observation in context.observations:
            symbol = str(observation["symbol"])
            feature = _mapping(features.get(symbol))
            entities.append({
                **observation,
                "coverage_state": feature.get(
                    "coverage_state", observation.get("coverage_state")
                ),
                "leading_feature": feature,
                "prior_leading": prior.get(symbol, {}),
                "recent_leading": _records(recent.get(symbol)),
            })
        results = score_entities(
            _BoardHotspotLeadingScorer(), entities,
            prior_by_symbol=prior, recent_by_symbol=recent,
        )
        _apply_leading_visibility(
            results,
            _mapping(context.dependencies["board_names"]),
            _mapping(context.dependencies["market_liquidity_context"]),
            prior,
            _mapping(context.dependencies["board_capacity_features"]),
            _mapping(context.dependencies["board_theme_profiles"]),
        )
        return ObservationSystemExecution(
            self.system_id, self.version, self.entity_scope, results,
        )


class _BoardHotspotLeadingScorer:
    system_id = BOARD_HOTSPOT_LEADING_SYSTEM
    version = BOARD_HOTSPOT_LEADING_VERSION
    entity_scope = "board"

    def score(self, entity: Mapping[str, object]) -> dict[str, object]:
        feature = _mapping(entity.get("leading_feature"))
        current = build_leading_sample(feature)
        recent_results = _records(entity.get("recent_leading"))
        samples = [*_prior_samples(recent_results), current][-LEADING_WINDOW_SESSIONS:]
        prior = _mapping(entity.get("prior_leading"))
        previous = samples[-2] if len(samples) >= 2 else {}

        deltas = {
            "price": _delta(current, previous, "return_5"),
            "relative_strength": _delta(current, previous, "relative_strength_5"),
            "activity": _delta(current, previous, "volume_persistence_5"),
            "breadth": _delta(current, previous, "positive_return_5_ratio"),
        }
        dimensions = leading_dimensions(current, deltas)
        components = leading_components(current, deltas, dimensions)
        penalties = leading_penalties(current)
        raw_score = max(0.0, min(
            100.0,
            sum(components.values())
            - sum(float(item["points"]) for item in penalties),
        ))
        candidate = raw_score >= 48 and sum(dimensions.values()) >= 3
        sample = {
            **current,
            "score": round(raw_score, 2),
            "candidate": candidate,
            "dimensions": dimensions,
            "deltas": {key: round(value, 6) if value is not None else None
                       for key, value in deltas.items()},
        }
        trajectory_samples = [*samples[:-1], sample]
        trajectory = leading_trajectory(trajectory_samples)
        prior_candidate_count = sum(
            bool(item.get("candidate")) for item in samples[:-1]
        )
        recent_two_candidates = sum(
            bool(item.get("candidate")) for item in [*samples[:-1], sample][-2:]
        )
        acceleration_count = sum((
            (deltas["price"] or 0.0) >= .006,
            (deltas["relative_strength"] or 0.0) >= .006,
            (deltas["activity"] or 0.0) >= .06,
            (deltas["breadth"] or 0.0) >= .04,
        ))
        prior_visible = bool(prior.get("leading_visible"))
        confirmed = bool(current.get("objective_confirmed"))
        overextended = bool(current.get("overextended"))
        if entity.get("coverage_state") != "complete" or len(samples) < 2:
            state = "watch" if candidate else "invalidated"
        elif confirmed:
            state = "launch-confirmed"
        elif overextended:
            state = "invalidated"
        elif (
            candidate and recent_two_candidates == 2
            and acceleration_count >= 2 and raw_score >= 58
            and bool(trajectory["eligible"])
            and structure_is_actionable(current)
        ):
            state = "strengthening"
        elif candidate:
            state = "watch"
        else:
            state = "invalidated"
        eligible = state == "strengthening"
        prior_score = _number(prior.get("total_score"))
        delta = raw_score - prior_score if prior_score is not None else 0.0
        direction = "new" if prior_score is None else (
            "strengthening" if delta >= 2 else
            "declining" if delta <= -2 else "stable"
        )
        streak = int(_number(prior.get("leading_streak")) or 0) + 1 \
            if state in {"watch", "strengthening"} else 0
        disqualifiers = []
        if entity.get("coverage_state") != "complete":
            disqualifiers.append("incomplete-board-series")
        if overextended:
            disqualifiers.append("overextended-before-admission")
        if bool(trajectory["late_pulse"]):
            disqualifiers.append("late-short-term-pulse")
        if not structure_is_actionable(current):
            disqualifiers.append("weak-structure-proximity")
        if confirmed and not prior_visible:
            disqualifiers.append("first-seen-after-confirmation")
        risks = [str(item["code"]) for item in penalties]
        summary = _leading_summary(state, current, deltas)
        return {
            "symbol": str(entity["symbol"]).upper(),
            "eligible": eligible and not disqualifiers,
            "total_score": round(raw_score, 2),
            "raw_score": round(raw_score, 2),
            "grade": score_grade(raw_score),
            "verdict": _state_label(state),
            "summary": summary,
            "risk_summary": "；".join(risks[:3]) if risks else "未发现单日脉冲或过度延伸。",
            "components": {key: round(value, 2) for key, value in components.items()},
            "penalties": penalties,
            "disqualifiers": disqualifiers,
            "hard_events": [],
            "leading_state": state,
            "leading_streak": streak,
            "leading_prior_candidate_sessions": prior_candidate_count,
            "leading_acceleration_count": acceleration_count,
            "leading_dimensions": dimensions,
            "leading_deltas": sample["deltas"],
            "leading_sample": sample,
            "leading_trajectory": trajectory,
            "leading_objective_confirmed": confirmed,
            "leading_carry_confirmation": confirmed and prior_visible,
            "score_direction": direction,
            "score_delta": round(delta, 2),
            "setup_path": current.get("shape_path"),
            "limit_up_count": current.get("limit_up_count"),
            "broken_up_count": current.get("broken_up_count"),
            "max_limit_up_streak": current.get("max_limit_up_streak"),
            "consecutive_limit_up_count": current.get(
                "consecutive_limit_up_count"
            ),
            "active_limit_up_members_5": current.get(
                "active_limit_up_members_5"
            ),
            "failed_limit_ratio": current.get("failed_limit_ratio"),
            "positive_return_5_ratio": current.get("positive_return_5_ratio"),
        }


def _apply_leading_visibility(
    results: list[dict[str, object]], names: Mapping[str, object],
    market: Mapping[str, object], prior: Mapping[str, Mapping[str, object]],
    board_capacities: Mapping[str, object],
    theme_profiles: Mapping[str, object],
) -> None:
    prior_market = next(iter(prior.values()), {})
    seats, seat_streak = stabilize_seat_budget(market, prior_market)
    representatives: dict[str, dict[str, object]] = {}
    for result in results:
        symbol = str(result["symbol"])
        name = str(names.get(symbol) or symbol)
        theme = _mapping(theme_profiles.get(symbol))
        theme_id = str(theme.get("theme_id") or canonical_board_name(name))
        capacity = _mapping(board_capacities.get(symbol))
        capacity_fit = market_capacity_fit(capacity, market)
        prior_result = _mapping(prior.get(symbol))
        market_gate = _market_leading_admission(result, market, capacity_fit)
        carry_confirmation = (
            str(result.get("leading_state")) == "launch-confirmed"
            and bool(prior_result.get("leading_visible"))
        )
        prior_visible_streak = int(
            _number(prior_result.get("leading_visible_streak")) or 0
        )
        hold_incumbent = (
            bool(prior_result.get("leading_visible"))
            and str(result.get("leading_state")) == "watch"
            and prior_visible_streak < 3
            and bool(market_gate["eligible"])
        )
        visibility_score = (
            (_number(result.get("total_score")) or 0.0)
            + float(capacity_fit["fit_score"])
            + (6.0 if str(result.get("leading_state")) == "strengthening" else 0.0)
            + (8.0 if bool(prior_result.get("leading_visible")) else 0.0)
        )
        result.update({
            "theme_registry_version": theme.get("registry_version"),
            "theme_name": theme.get("theme_name") or name,
            "theme_parent_id": theme.get("parent_theme_id"),
            "theme_parent_name": theme.get("parent_theme_name"),
            "theme_match_method": theme.get("match_method"),
            "theme_signal_eligible": theme.get("signal_eligible", True),
            "leading_visible": False,
            "leading_rank": None,
            "leading_visible_streak": 0,
            "leading_slot_limit": seats,
            "radar_slot_limit": seats,
            "leading_visibility_score": round(visibility_score, 2),
            "market_liquidity_capacity": market.get("capacity_tier"),
            "market_liquidity_direction": market.get("direction"),
            "market_liquidity_regime": market.get("regime"),
            "market_liquidity_raw_seats": market.get("raw_visible_seats"),
            "market_liquidity_seat_streak": seat_streak,
            "market_liquidity_ratio": market.get("volume_ratio_5_20"),
            "board_capacity_tier": capacity.get("capacity_tier"),
            "board_turnover_intensity": capacity.get("turnover_intensity"),
            "capacity_compatible": capacity_fit["compatible"],
            "capacity_market_preferred": capacity_fit["market_compatible"],
            "capacity_fit_score": capacity_fit["fit_score"],
            "market_admission_eligible": market_gate["eligible"],
            "market_admission_reasons": market_gate["reasons"],
            "leading_carry_confirmation": carry_confirmation,
            "leading_incumbent_hold": hold_incumbent,
        })
        if not (
            (bool(result.get("eligible")) or carry_confirmation or hold_incumbent)
            and bool(capacity_fit["compatible"])
            and bool(market_gate["eligible"])
            and bool(theme.get("signal_eligible", True))
        ):
            continue
        existing = representatives.get(theme_id)
        if existing is None or _leading_visibility_key(result) > _leading_visibility_key(existing):
            representatives[theme_id] = result
    ranked = sorted(
        representatives.values(), key=_leading_visibility_key, reverse=True,
    )
    for rank, result in enumerate(ranked[:seats], 1):
        result["leading_visible"] = True
        result["leading_rank"] = rank
        prior_result = _mapping(prior.get(str(result["symbol"])))
        result["leading_visible_streak"] = (
            int(_number(prior_result.get("leading_visible_streak")) or 0) + 1
            if bool(prior_result.get("leading_visible")) else 1
        )


def _market_leading_admission(
    result: Mapping[str, object], market: Mapping[str, object],
    capacity_fit: Mapping[str, object],
) -> dict[str, object]:
    tier = str(market.get("capacity_tier") or "unknown")
    direction = str(market.get("direction") or "neutral")
    dimensions = _mapping(result.get("leading_dimensions"))
    path = str(result.get("setup_path") or "none")
    reasons: list[str] = []
    if not bool(dimensions.get("leader_formation")):
        reasons.append("missing-persistent-leader")
    if not bool(capacity_fit.get("market_compatible")):
        reasons.append("board-capacity-mismatches-market")
    if tier in {"low", "unknown"}:
        if not (
            bool(dimensions.get("member_diffusion")) or path != "none"
        ):
            reasons.append("low-capacity-market-needs-diffusion-or-shape")
    if direction == "contracting":
        if path == "none":
            reasons.append("contracting-market-needs-explicit-shape")
        if not bool(dimensions.get("member_diffusion")):
            reasons.append("contracting-market-needs-member-diffusion")
    return {"eligible": not reasons, "reasons": reasons}


def _leading_visibility_key(result: Mapping[str, object]) -> tuple[float, float, str]:
    return (
        _number(result.get("leading_visibility_score")) or 0.0,
        _number(result.get("score_delta")) or 0.0,
        str(result.get("symbol") or ""),
    )


def _leading_summary(
    state: str, current: Mapping[str, object], deltas: Mapping[str, float | None],
) -> str:
    rs_delta = (deltas.get("relative_strength") or 0.0) * 100
    breadth_delta = (deltas.get("breadth") or 0.0) * 100
    return (
        f"阶段：{_state_label(state)}；近5日涨幅"
        f"{(_number(current.get('return_5')) or 0.0) * 100:.1f}%，"
        f"相对强度变化{rs_delta:+.1f}pct，成分扩散变化{breadth_delta:+.1f}pct。"
    )


def _state_label(state: str) -> str:
    return {
        "watch": "潜伏观察",
        "strengthening": "临界增强",
        "launch-confirmed": "启动确认",
        "invalidated": "失效",
    }.get(state, state)


def _prior_samples(
    recent_results: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    samples = []
    seen_dates: set[str] = set()
    for result in recent_results:
        effective_date = str(result.get("effective_date") or "")
        if effective_date and effective_date in seen_dates:
            continue
        sample = _mapping(result.get("leading_sample"))
        if sample:
            samples.append(sample)
        if effective_date:
            seen_dates.add(effective_date)
        if len(samples) >= LEADING_WINDOW_SESSIONS - 1:
            break
    return list(reversed(samples))


def _delta(
    current: Mapping[str, object], previous: Mapping[str, object], key: str,
) -> float | None:
    current_value = _number(current.get(key))
    previous_value = _number(previous.get(key))
    if current_value is None or previous_value is None:
        return None
    return current_value - previous_value


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _records(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None
