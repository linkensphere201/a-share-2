"""First deterministic daily mean-reversion review system."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json

from stock_harness.review_scoring import score_grade
from stock_harness.review_systems.contracts import (
    ANALYSIS_RESULT_CONTRACT_VERSION,
    AnalysisSystemContext,
    AnalysisSystemDefinition,
)


MEAN_REVERSION_SYSTEM_ID = "mean-reversion"
MEAN_REVERSION_VERSION = "mean-reversion-daily-v3"


class MeanReversionReviewSystem:
    definition = AnalysisSystemDefinition(
        system_id=MEAN_REVERSION_SYSTEM_ID,
        version=MEAN_REVERSION_VERSION,
        display_name="均值回归",
        supported_scopes=("market", "board", "stock"),
        setup_families=("directional-pullback", "oversold-exhaustion", "none"),
        dependencies=("mean_reversion_facts",),
    )

    def analyze(self, context: AnalysisSystemContext) -> Sequence[dict[str, object]]:
        pending = []
        for scope in self.definition.supported_scopes:
            for entity in context.entities_by_scope.get(scope, ()):
                pending.append(analyze_mean_reversion_entity(scope, entity))
        groups: dict[tuple[str, str], list[dict[str, object]]] = {}
        for result in pending:
            groups.setdefault(
                (str(result["entity_scope"]), str(result["setup_family"])), []
            ).append(result)
        output = []
        prior = context.prior_results.get(MEAN_REVERSION_SYSTEM_ID, {})
        recent = context.recent_results.get(MEAN_REVERSION_SYSTEM_ID, {})
        for group_key, values in sorted(groups.items()):
            values.sort(key=lambda item: (
                not bool(_mapping(item["eligibility"]).get("eligible")),
                -_number(_mapping(item["scorecard"]).get("total_score")),
                str(item["symbol"]),
            ))
            digest = hashlib.sha256(json.dumps(
                sorted(str(item["entity_key"]) for item in values),
                ensure_ascii=False, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            for rank, result in enumerate(values, 1):
                entity_key = str(result["entity_key"])
                candidates = list(recent.get(
                    entity_key, recent.get(str(result["symbol"]), ()),
                ))
                history = _compatible_history(result, candidates)[:5]
                current_prior = history[0] if history else None
                if current_prior is None:
                    fallback = prior.get(entity_key) or prior.get(str(result["symbol"]))
                    current_prior = fallback if _is_compatible_history(result, fallback) else None
                output.append(_finalize_result(
                    result, rank, len(values), digest, current_prior, history,
                ))
        return output

    def replay_definition(self) -> Mapping[str, object]:
        return {
            "labels": [
                "target-before-invalidation", "time-to-center", "mfe", "mae",
                "maximum-holding-expiry", "structural-break",
            ],
            "segmentation": ["setup_family", "entity_scope", "market_regime"],
            "future_inputs_in_selection": False,
        }


def analyze_mean_reversion_entity(
    scope: str, entity: Mapping[str, object],
) -> dict[str, object]:
    """Analyze one point-in-time entity for replay and production orchestration."""
    symbol = str(entity.get("symbol") or "").upper()
    if not symbol:
        raise ValueError("mean-reversion entity symbol is required")
    facts = _mapping(entity.get("mean_reversion"))
    family = str(facts.get("setup_family") or "none")
    state = str(facts.get("state") or "unqualified")
    center = _mapping(facts.get("center"))
    exhaustion = _mapping(facts.get("exhaustion"))
    confirmation = _mapping(facts.get("confirmation"))
    price_volume = _mapping(facts.get("price_volume"))
    targets = [
        dict(value) for value in _sequence(facts.get("targets"))
        if isinstance(value, Mapping)
    ]
    disqualifiers = [str(value) for value in _sequence(facts.get("disqualifiers"))]
    if facts.get("coverage_state") != "complete":
        disqualifiers.append("incomplete-data")
    if family == "none":
        disqualifiers.append("no-mean-reversion-setup")
    if scope == "stock" and family == "oversold-exhaustion":
        disqualifiers.append("stock-falling-knife-disabled-v1")
    if state != "reversal-confirmed":
        disqualifiers.append("price-confirmation-pending")
    selected = next((target for target in targets if (
        _optional_number(target.get("stressed_risk_reward_ratio")) is not None
        and _number(target.get("stressed_risk_reward_ratio")) >= 3
    )), None)
    if selected is None:
        disqualifiers.append("no-credible-target-at-3r")
    entry = _optional_number(confirmation.get("entry_price"))
    invalidation = _optional_number(facts.get("invalidation_price"))
    selected_price = _optional_number(selected.get("price")) if selected else None
    if entry is None or invalidation is None or selected_price is None or not (
        selected_price > entry > invalidation
    ):
        disqualifiers.append("invalid-long-price-ordering")

    center_points = 20.0 if bool(center.get("stable")) else 0.0
    if family == "directional-pullback" and facts.get("parent_trend") != "up":
        center_points = min(center_points, 6.0)
        disqualifiers.append("parent-uptrend-not-qualified")
    deviation = abs(_number(facts.get("recent_low_deviation_atr")))
    deviation_threshold = 1.5 if family == "oversold-exhaustion" else .65
    deviation_points = (
        min(15.0, 6.0 + max(0.0, deviation - deviation_threshold) * 6.0)
        if family != "none" and deviation >= deviation_threshold else 0.0
    )
    path = str(price_volume.get("path") or "neutral")
    volume_points = {
        "volume-backed-reclaim": 20.0,
        "capitulation-absorption": 18.0,
        "shrinking-volume-stabilization": 14.0,
        "neutral": 8.0,
        "shrinking-volume-rebound": 4.0,
        "expanding-volume-decline": 0.0,
        "volume-backed-structural-break": 0.0,
    }.get(path, 6.0)
    exhaustion_count = int(_number(exhaustion.get("signal_count")))
    exhaustion_points = min(12.5, exhaustion_count * 2.5)
    if bool(confirmation.get("confirmed")):
        exhaustion_points = min(15.0, exhaustion_points + 2.5)
    best_rr = max(
        (_number(target.get("stressed_risk_reward_ratio")) for target in targets),
        default=0.0,
    )
    rr_points = min(30.0, best_rr * 7.5)
    components = {
        "center_regime": round(center_points, 2),
        "normalized_deviation": round(deviation_points, 2),
        "price_volume": round(volume_points, 2),
        "exhaustion_confirmation": round(exhaustion_points, 2),
        "risk_reward": round(rr_points, 2),
    }
    penalties = []
    if path in {"expanding-volume-decline", "volume-backed-structural-break"}:
        penalties.append({"code": path, "points": 20.0})
    if path == "shrinking-volume-rebound":
        penalties.append({"code": "weak-shrinking-volume-rebound", "points": 8.0})
    total = max(0.0, min(100.0, sum(components.values()) - sum(
        _number(item["points"]) for item in penalties
    )))
    if family == "none":
        total = 0.0
    disqualifiers = sorted(set(disqualifiers))
    eligible = not disqualifiers
    verdict = _verdict(family, state, eligible)
    summary = _summary(family, state, facts, selected)
    risk_summary = _risk_summary(disqualifiers, facts)
    evidence = _evidence(facts)
    opportunity = {
        "state": state, "direction": "long", "entry_price": entry,
        "confirmation_price": _optional_number(confirmation.get("boundary_price")),
        "invalidation_price": invalidation, "targets": targets,
        "selected_target_label": selected.get("label") if selected else None,
        "selected_target_price": selected_price,
        "raw_risk_reward": (
            _optional_number(selected.get("risk_reward_ratio")) if selected else None
        ),
        "stressed_risk_reward": (
            _optional_number(selected.get("stressed_risk_reward_ratio")) if selected else None
        ),
        "maximum_holding_sessions": 20 if family == "directional-pullback" else 10,
    }
    result = {
        "contract_version": ANALYSIS_RESULT_CONTRACT_VERSION,
        "system_id": MEAN_REVERSION_SYSTEM_ID,
        "system_version": MEAN_REVERSION_VERSION,
        "setup_family": family, "timeframe": "daily",
        "entity_scope": scope, "entity_key": str(entity.get("entity_key") or symbol),
        "symbol": symbol,
        "eligibility": {
            "eligible": eligible, "state": state,
            "rejection_reasons": disqualifiers,
        },
        "scorecard": {
            "total_score": round(total, 2), "grade": score_grade(total),
            "dimensions": components, "penalties": penalties,
            "ranking_universe": f"{scope}:{family}:daily",
        },
        "conclusion": {
            "verdict": verdict, "summary": summary,
            "sections": _conclusion_sections(facts, opportunity),
            "risks": disqualifiers,
            "risk_summary": risk_summary,
        },
        "evidence": evidence,
        "opportunity": opportunity,
        "chart_projection": _chart_projection(facts, targets),
        "diagnostics": {
            "facts_version": facts.get("version"),
            "coverage_state": facts.get("coverage_state"),
            "warnings": [], "input_digest": entity.get("input_digest"),
        },
        "system_payload": {
            "mean_reversion": facts,
            "price_volume_path": path,
            "exhaustion_signal_count": exhaustion_count,
        },
    }
    return result


def _compatible_history(
    current: Mapping[str, object], candidates: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    return [
        candidate for candidate in candidates
        if _is_compatible_history(current, candidate)
    ]


def _is_compatible_history(
    current: Mapping[str, object], candidate: Mapping[str, object] | None,
) -> bool:
    if not candidate:
        return False
    current_scorecard = _mapping(current.get("scorecard"))
    candidate_scorecard = _mapping(candidate.get("scorecard"))
    return all((
        str(candidate.get("setup_family") or "") == str(current.get("setup_family") or ""),
        str(candidate.get("entity_scope") or "") == str(current.get("entity_scope") or ""),
        str(candidate.get("timeframe") or "") == str(current.get("timeframe") or ""),
        str(candidate_scorecard.get("ranking_universe") or "")
        == str(current_scorecard.get("ranking_universe") or ""),
    ))


def _finalize_result(
    result: dict[str, object], rank: int, participant_count: int,
    universe_digest: str, prior: Mapping[str, object] | None,
    recent: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    scorecard = _mapping(result["scorecard"])
    eligibility = _mapping(result["eligibility"])
    conclusion = _mapping(result["conclusion"])
    opportunity = _mapping(result["opportunity"])
    prior_score = _prior_score(prior)
    current_score = _number(scorecard.get("total_score"))
    change = (
        "首次分析" if prior_score is None
        else "较上轮增强" if current_score >= prior_score + 3
        else "较上轮减弱" if current_score <= prior_score - 3
        else "较上轮稳定"
    )
    result["comparison"] = {
        "prior_total_score": prior_score,
        "score_change": round(current_score - prior_score, 2) if prior_score is not None else None,
        "prior_state": _prior_state(prior),
        "recent": [
            {
                "effective_date": _date_value(value.get("effective_date")),
                "total_score": _prior_score(value),
                "state": _prior_state(value),
            } for value in recent
        ],
    }
    result["history"] = [
        {
            "run_id": value.get("run_id"),
            "entity_key": value.get("entity_key"),
            "effective_date": _date_value(value.get("effective_date")),
            "total_score": _prior_score(value),
            "grade": value.get("grade"),
            "rank": value.get("rank"),
            "eligible": value.get("eligible"),
        } for value in recent
    ]
    result.update({
        "scorer_version": result["system_version"],
        "eligible": bool(eligibility.get("eligible")),
        "total_score": current_score, "grade": scorecard.get("grade"),
        "rank": rank, "participant_count": participant_count,
        "ranking_universe_digest": universe_digest,
        "verdict": str(conclusion.get("verdict") or ""),
        "summary": str(conclusion.get("summary") or ""),
        "risk_summary": str(conclusion.get("risk_summary") or "暂无显著结构风险。"),
        "change_summary": change,
        "components": scorecard.get("dimensions", {}),
        "penalties": scorecard.get("penalties", []),
        "disqualifiers": eligibility.get("rejection_reasons", []),
        "hard_events": [],
        "evidence_refs": [
            value.get("evidence_id") for value in result["evidence"]
            if isinstance(value, Mapping)
        ],
        "selected_target_label": opportunity.get("selected_target_label"),
        "selected_target_price": opportunity.get("selected_target_price"),
        "raw_risk_reward": opportunity.get("raw_risk_reward"),
        "stressed_risk_reward": opportunity.get("stressed_risk_reward"),
    })
    return result


def _evidence(facts: Mapping[str, object]) -> list[dict[str, object]]:
    center = _mapping(facts.get("center"))
    confirmation = _mapping(facts.get("confirmation"))
    return [{
        "evidence_id": "mr:center", "kind": "moving-center",
        "label": "运动中心", "value": center.get("price"),
    }, {
        "evidence_id": "mr:deviation", "kind": "atr-deviation",
        "label": "ATR偏离", "value": facts.get("recent_low_deviation_atr"),
    }, {
        "evidence_id": "mr:exhaustion", "kind": "momentum-exhaustion",
        "label": "下跌动能衰竭", "value": facts.get("exhaustion"),
    }, {
        "evidence_id": "mr:price-volume", "kind": "price-volume-path",
        "label": "量价路径", "value": facts.get("price_volume"),
    }, {
        "evidence_id": "mr:confirmation", "kind": "confirmation-boundary",
        "label": "确认边界", "value": confirmation.get("boundary_price"),
    }, {
        "evidence_id": "mr:invalidation", "kind": "invalidation",
        "label": "失效边界", "value": facts.get("invalidation_price"),
    }]


def _chart_projection(
    facts: Mapping[str, object], targets: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    chart = _mapping(facts.get("chart"))
    confirmation = _mapping(facts.get("confirmation"))
    projections = [{
        "projection_id": "mr:center", "kind": "series-line",
        "role": "moving-center", "label": "EMA20运动中心",
        "points": chart.get("center_points", []),
    }, {
        "projection_id": "mr:deviation-band", "kind": "series-band",
        "role": "atr-deviation-band", "label": "±1.5 ATR偏离带",
        "upper_points": chart.get("upper_band_points", []),
        "lower_points": chart.get("lower_band_points", []),
    }, {
        "projection_id": "mr:confirmation", "kind": "price-line",
        "role": "confirmation", "label": "确认位",
        "price": confirmation.get("boundary_price"),
    }, {
        "projection_id": "mr:invalidation", "kind": "price-line",
        "role": "invalidation", "label": "失效位",
        "price": facts.get("invalidation_price"),
    }]
    projections.extend({
        "projection_id": f"mr:target:{target.get('label')}",
        "kind": "price-line", "role": "target",
        "label": str(target.get("label") or "目标"), "price": target.get("price"),
    } for target in targets)
    return projections


def _conclusion_sections(
    facts: Mapping[str, object], opportunity: Mapping[str, object],
) -> list[dict[str, object]]:
    center = _mapping(facts.get("center"))
    momentum = _mapping(facts.get("momentum"))
    exhaustion = _mapping(facts.get("exhaustion"))
    price_volume = _mapping(facts.get("price_volume"))
    return [
        {"code": "regime", "title": "母趋势与中心", "text": (
            f"母趋势{facts.get('parent_trend')}；运动中心{center.get('price')}，"
            f"稳定性{'通过' if center.get('stable') else '不足'}。"
        ), "evidence_refs": ["mr:center"]},
        {"code": "deviation", "title": "偏离与动能", "text": (
            f"最近低点偏离{facts.get('recent_low_deviation_atr')} ATR；"
            f"负动能{'衰减' if momentum.get('negative_decelerating') else '未确认衰减'}，"
            f"衰竭证据{exhaustion.get('signal_count', 0)}项。"
        ), "evidence_refs": ["mr:deviation", "mr:exhaustion"]},
        {"code": "price-volume", "title": "量价", "text": (
            f"量价路径：{price_volume.get('path', 'neutral')}。"
        ), "evidence_refs": ["mr:price-volume"]},
        {"code": "execution", "title": "确认、失效与空间", "text": (
            f"确认位{opportunity.get('confirmation_price')}，"
            f"失效位{opportunity.get('invalidation_price')}，"
            f"压力盈亏比{opportunity.get('stressed_risk_reward')}。"
        ), "evidence_refs": ["mr:confirmation", "mr:invalidation"]},
    ]


def _summary(
    family: str, state: str, facts: Mapping[str, object],
    selected: Mapping[str, object] | None,
) -> str:
    family_label = {
        "directional-pullback": "上涨母趋势内的次级折返",
        "oversold-exhaustion": "超跌衰竭",
        "none": "未形成均值回归结构",
    }.get(family, family)
    rr = selected.get("stressed_risk_reward_ratio") if selected else None
    return (
        f"{family_label}，当前为{_state_label(state)}；"
        f"最近低点偏离{facts.get('recent_low_deviation_atr')} ATR"
        + (f"，最近可用目标压力盈亏比{rr}:1。" if rr is not None else "，暂无达到门槛的目标空间。")
    )


def _risk_summary(disqualifiers: Sequence[str], facts: Mapping[str, object]) -> str:
    if disqualifiers:
        return "；".join(disqualifiers[:4])
    return "结构与量价确认完整；仍需执行失效位和最大持有期。"


def _verdict(family: str, state: str, eligible: bool) -> str:
    if eligible:
        return "均值回归机会已确认"
    if state == "structural-break":
        return "结构破坏，不做均值回归"
    if state == "exhaustion-watch":
        return "衰竭观察，等待价格确认"
    if family != "none":
        return "偏离成立，尚未确认"
    return "当前无均值回归机会"


def _state_label(value: str) -> str:
    return {
        "unqualified": "不符合", "stable-center": "中心稳定",
        "deviation-building": "偏离扩大", "extreme-pending": "极端偏离",
        "exhaustion-watch": "衰竭观察", "reversal-confirmed": "反转确认",
        "reverting": "回归进行中", "target-reached": "到达目标",
        "continued-divergence": "持续背离", "structural-break": "结构断裂",
    }.get(value, value)


def _prior_score(value: Mapping[str, object] | None) -> float | None:
    if value is None:
        return None
    direct = _optional_number(value.get("total_score"))
    if direct is not None:
        return direct
    return _optional_number(_mapping(value.get("scorecard")).get("total_score"))


def _prior_state(value: Mapping[str, object] | None) -> str | None:
    if value is None:
        return None
    direct = value.get("state")
    if direct is not None:
        return str(direct)
    return str(_mapping(value.get("eligibility")).get("state") or "") or None


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _optional_number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _number(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _date_value(value: object) -> object:
    return value.isoformat() if hasattr(value, "isoformat") else value
