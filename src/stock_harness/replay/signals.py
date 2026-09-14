"""Adapt complete system results without reconstructing missing trade boundaries."""

from collections.abc import Mapping
from datetime import date

from stock_harness.replay.contracts import ExitPlan, FrozenSignal


def signal_from_analysis_result(
    result: Mapping[str, object], cutoff: date, *, reference_close: float,
) -> FrozenSignal:
    opportunity = result.get("opportunity")
    if not isinstance(opportunity, Mapping):
        raise ValueError("complete analysis result requires opportunity data")
    system_id = str(result["system_id"])
    stop = opportunity.get("invalidation_price")
    target = opportunity.get("selected_target_price")
    raw_plan = opportunity.get("exit_plan")
    plan = ExitPlan(**raw_plan) if isinstance(raw_plan, Mapping) else None
    scorecard = result.get("scorecard")
    score = scorecard.get("total_score") if isinstance(scorecard, Mapping) else result.get("total_score")
    return FrozenSignal(
        system_id=system_id, system_version=str(result["system_version"]),
        symbol=str(result["symbol"]), scope=str(result["entity_scope"]),
        signal_date=cutoff, direction=str(opportunity.get("direction") or "long"),
        reference_close=reference_close,
        exit_plan=plan,
        score=float(score) if score is not None else None,
        invalidation_price=float(stop) if stop is not None else None,
        selected_target_price=float(target) if target is not None else None,
        setup_family=str(result.get("setup_family") or system_id),
        observation_sessions=opportunity.get("observation_sessions"),
        observation_id=opportunity.get("observation_id"),
        metadata={
            "state": opportunity.get("state"),
            "maximum_holding_sessions": opportunity.get("maximum_holding_sessions"),
            "observation_targets": opportunity.get("targets", ()),
        },
    )
