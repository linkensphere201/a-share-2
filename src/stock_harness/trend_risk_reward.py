"""Single trend-space policy; execution eligibility remains a separate decision."""

from math import isfinite
from decimal import Decimal
from typing import Mapping

SCENARIO_VERSION = "structural-trade-scenario-v5-nearest-raw-rr"
POLICY_VERSION = "trend-space-v1-raw-strict"
QUALIFIED_THRESHOLD = 1.0
OPPORTUNITY_THRESHOLD = 2.0


def grade_ratio(ratio: float | None) -> str:
    if ratio is None or not isfinite(ratio) or ratio < 0:
        return "unavailable"
    if ratio > OPPORTUNITY_THRESHOLD:
        return "opportunity"
    if ratio > QUALIFIED_THRESHOLD:
        return "qualified"
    return "insufficient"


def evaluate_trend_space(scenario: Mapping[str, object]) -> dict[str, object]:
    """Recompute from unrounded prices, never trust a persisted eligibility flag."""
    result: dict[str, object] = {
        "policy_version": POLICY_VERSION, "status": "unavailable",
        "reason": None, "risk_reward_ratio": None,
        "reward_distance": None, "risk_distance": None,
        "qualified": False, "opportunity": False,
    }
    if scenario.get("contract_version") != SCENARIO_VERSION:
        return {**result, "reason": "legacy-analysis-recalculate"}
    if scenario.get("qualification_blocked"):
        return {**result, "reason": "price-basis-unverified"}
    if scenario.get("state") in {"invalidated", "failed", "stale", "no-entry"}:
        return {**result, "reason": "inactive-or-missing-structure"}
    targets = scenario.get("targets")
    target = targets[0] if isinstance(targets, list) and targets else None
    if not isinstance(target, Mapping):
        return {**result, "reason": "missing-nearest-target"}
    values = [scenario.get("entry_price"), scenario.get("invalidation_price"), target.get("price")]
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
               and isfinite(v) and v > 0 for v in values):
        return {**result, "reason": "invalid-price"}
    entry, stop, price = (Decimal(str(v)) for v in values)
    if scenario.get("direction") == "long":
        reward, risk = price - entry, entry - stop
    elif scenario.get("direction") == "short":
        reward, risk = entry - price, stop - entry
    else:
        return {**result, "reason": "invalid-direction"}
    if risk <= 0 or reward <= 0:
        return {**result, "reason": "invalid-price-ordering"}
    ratio = float(reward / risk)
    status = grade_ratio(ratio)
    return {**result, "status": status, "risk_reward_ratio": ratio,
            "reward_distance": float(reward), "risk_distance": float(risk),
            "qualified": status in {"qualified", "opportunity"},
            "opportunity": status == "opportunity"}
