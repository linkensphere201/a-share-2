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
    plan = None
    if stop is not None and target is not None:
        plan = ExitPlan(
            f"{system_id}:selected-target-full-exit-v1", float(stop), float(target),
            opportunity.get("maximum_holding_sessions"),
        )
        plan.validate()
    return FrozenSignal(
        system_id=system_id, system_version=str(result["system_version"]),
        symbol=str(result["symbol"]), scope=str(result["entity_scope"]),
        signal_date=cutoff, direction=str(opportunity.get("direction") or "long"),
        reference_close=reference_close,
        invalidation_price=float(stop) if stop is not None else None,
        selected_target_price=float(target) if target is not None else None,
        setup_family=str(result.get("setup_family") or system_id),
        exit_plan=plan,
    )
