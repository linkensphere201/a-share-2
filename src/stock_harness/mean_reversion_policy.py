"""System-owned decision policy for deterministic daily mean reversion."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


POLICY_VERSION = "mean-reversion-policy-v1"
MINIMUM_CONFIRMATION_QUALITY = 55.0
MINIMUM_TRADEABLE_STRESSED_RR = 1.0
STRESSED_ASYMMETRY_THRESHOLD = 3.0


def build_mean_reversion_decision(
    scope: str, facts: Mapping[str, object],
) -> dict[str, object]:
    """Separate setup validity, confirmation, execution, and asymmetry tiers."""
    family = str(facts.get("setup_family") or "none")
    state = str(facts.get("state") or "unqualified")
    confirmation = _mapping(facts.get("confirmation"))
    targets = [dict(value) for value in _sequence(facts.get("targets"))
               if isinstance(value, Mapping)]
    entry = _optional_number(confirmation.get("entry_price"))
    invalidation = _optional_number(facts.get("invalidation_price"))
    quality = _confirmation_quality(confirmation)

    validity_reasons = [str(value) for value in _sequence(facts.get("disqualifiers"))]
    if facts.get("coverage_state") != "complete":
        validity_reasons.append("incomplete-data")
    if family == "none":
        validity_reasons.append("no-mean-reversion-setup")
    if scope == "stock" and family == "oversold-exhaustion":
        validity_reasons.append("stock-falling-knife-disabled-v1")
    if family == "directional-pullback" and facts.get("parent_trend") != "up":
        validity_reasons.append("parent-uptrend-not-qualified")
    if bool(_mapping(facts.get("regime")).get("persistent_one_way_decline")):
        validity_reasons.append("persistent-decline-regime")

    confirmation_reasons = []
    if state != "reversal-confirmed" or not bool(confirmation.get("confirmed")):
        confirmation_reasons.append("price-confirmation-pending")
    if quality < MINIMUM_CONFIRMATION_QUALITY:
        confirmation_reasons.append("confirmation-quality-insufficient")

    primary = next((target for target in targets
                    if target.get("target_class") in {None, "mean-reversion"}), None)
    extension = next((target for target in targets
                      if target.get("target_class") == "extension"), None)
    asymmetric = next((target for target in targets if (
        _optional_number(target.get("stressed_risk_reward_ratio")) is not None
        and _number(target.get("stressed_risk_reward_ratio")) >= STRESSED_ASYMMETRY_THRESHOLD
    )), None)
    primary_price = _optional_number(primary.get("price")) if primary else None
    execution_reasons = []
    if primary is None:
        execution_reasons.append("no-unconsumed-mean-reversion-target")
    if entry is None or invalidation is None or primary_price is None or not (
        primary_price > entry > invalidation
    ):
        execution_reasons.append("invalid-long-price-ordering")
    primary_rr = (
        _optional_number(primary.get("stressed_risk_reward_ratio")) if primary else None
    )
    if primary_rr is None or primary_rr < MINIMUM_TRADEABLE_STRESSED_RR:
        execution_reasons.append("near-target-reward-risk-below-1r")

    validity_reasons = sorted(set(validity_reasons))
    confirmation_reasons = sorted(set(confirmation_reasons))
    execution_reasons = sorted(set(execution_reasons))
    eligible = not validity_reasons and not confirmation_reasons and not execution_reasons
    tier = (
        "asymmetric-3r" if eligible and asymmetric is not None
        else "tradeable-reversion" if eligible
        else "confirmed-not-tradeable" if not validity_reasons and not confirmation_reasons
        else "confirmed-invalid" if not confirmation_reasons
        else "observation"
    )
    return {
        "policy_version": POLICY_VERSION,
        "eligible": eligible,
        "tier": tier,
        "validity": {"valid": not validity_reasons, "reasons": validity_reasons},
        "confirmation": {
            "passed": not confirmation_reasons,
            "quality_score": quality,
            "minimum_quality_score": MINIMUM_CONFIRMATION_QUALITY,
            "reasons": confirmation_reasons,
        },
        "execution": {
            "tradeable": not execution_reasons,
            "entry_price": entry,
            "invalidation_price": invalidation,
            "selected_target": primary,
            "extension_target": extension,
            "minimum_stressed_risk_reward": MINIMUM_TRADEABLE_STRESSED_RR,
            "reasons": execution_reasons,
        },
        "asymmetry": {
            "has_stressed_3r_target": asymmetric is not None,
            "threshold": STRESSED_ASYMMETRY_THRESHOLD,
            "target": asymmetric,
        },
        "rejection_reasons": sorted(set(
            validity_reasons + confirmation_reasons + execution_reasons
        )),
    }


def _confirmation_quality(confirmation: Mapping[str, object]) -> float:
    explicit = _optional_number(confirmation.get("quality_score"))
    if explicit is not None:
        return max(0.0, min(100.0, explicit))
    return 100.0 if bool(confirmation.get("confirmed")) else 0.0


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _optional_number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _number(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0
