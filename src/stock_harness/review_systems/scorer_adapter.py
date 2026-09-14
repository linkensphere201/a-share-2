"""Adapter from legacy deterministic scorers to the complete M16 result contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from stock_harness.review_scoring import ReviewScorer, score_entities
from stock_harness.review_systems.contracts import (
    ANALYSIS_RESULT_CONTRACT_VERSION,
    AnalysisSystemContext,
    AnalysisSystemDefinition,
)


class ScorerAnalysisSystemAdapter:
    def __init__(self, scorer: ReviewScorer, *, display_name: str) -> None:
        if scorer.entity_scope not in {"market", "board", "stock"}:
            raise ValueError("legacy scorer scope cannot be adapted to M16")
        self._scorer = scorer
        self.definition = AnalysisSystemDefinition(
            system_id=scorer.system_id,
            version=scorer.version,
            display_name=display_name,
            supported_scopes=(scorer.entity_scope,),
            setup_families=(scorer.system_id,),
        )

    def analyze(self, context: AnalysisSystemContext) -> Sequence[dict[str, object]]:
        scorer = self._scorer
        results = score_entities(
            scorer,
            context.entities_by_scope.get(scorer.entity_scope, ()),
            prior_by_symbol=context.prior_results.get(scorer.system_id),
            recent_by_symbol=context.recent_results.get(scorer.system_id),
        )
        return [_complete_result(value, scorer.system_id) for value in results]

    def replay_definition(self) -> Mapping[str, object]:
        return {
            "labels": ["eligible", "score", "rank", "hard-event-transition"],
            "future_inputs_in_selection": False,
        }


def _complete_result(
    value: Mapping[str, object], setup_family: str,
) -> dict[str, object]:
    eligible = bool(value.get("eligible"))
    state = str(value.get("setup_state") or ("eligible" if eligible else "waiting"))
    components = _mapping(value.get("components"))
    penalties = [
        dict(item) for item in _sequence(value.get("penalties"))
        if isinstance(item, Mapping)
    ]
    rejection_reasons = [str(item) for item in _sequence(value.get("disqualifiers"))]
    evidence_refs = [str(item) for item in _sequence(value.get("evidence_refs"))]
    opportunity = {
        "state": state,
        "direction": "long" if setup_family != "market-regime" else "environment",
        "entry_price": value.get("entry_price"),
        "confirmation_price": None,
        "invalidation_price": value.get("invalidation_price"),
        "targets": ([{
            "label": value.get("selected_target_label"),
            "price": value.get("selected_target_price"),
            "basis": "legacy-scorer-selected-target",
            "risk_reward_ratio": value.get("raw_risk_reward"),
            "stressed_risk_reward_ratio": value.get("stressed_risk_reward"),
        }] if value.get("selected_target_price") is not None else []),
        "selected_target_label": value.get("selected_target_label"),
        "selected_target_price": value.get("selected_target_price"),
        "raw_risk_reward": value.get("raw_risk_reward"),
        "stressed_risk_reward": value.get("stressed_risk_reward"),
    }
    complete = {
        **value,
        "contract_version": ANALYSIS_RESULT_CONTRACT_VERSION,
        "system_version": value.get("scorer_version"),
        "setup_family": setup_family,
        "timeframe": "daily",
        "eligibility": {
            "eligible": eligible, "state": state,
            "rejection_reasons": rejection_reasons,
        },
        "scorecard": {
            "total_score": value.get("total_score", 0),
            "grade": value.get("grade"),
            "dimensions": dict(components),
            "penalties": penalties,
            "ranking_universe": value.get("ranking_universe_digest"),
        },
        "conclusion": {
            "verdict": value.get("verdict", ""),
            "summary": value.get("summary", ""),
            "risk_summary": value.get("risk_summary", ""),
            "risks": rejection_reasons,
            "sections": [{
                "code": "primary",
                "title": "固定算法结论",
                "text": value.get("summary", ""),
                "evidence_refs": evidence_refs,
            }],
        },
        "evidence": [{
            "evidence_id": reference,
            "kind": "generated-analysis-reference",
            "label": reference,
            "value": reference,
        } for reference in evidence_refs],
        "opportunity": opportunity,
        "chart_projection": [],
        "diagnostics": {
            "adapter": "legacy-review-scorer-v1",
            "coverage_state": "complete",
            "warnings": [],
        },
        "system_payload": {"legacy_result": dict(value)},
    }
    return complete


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()
