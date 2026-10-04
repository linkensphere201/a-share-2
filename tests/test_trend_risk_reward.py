from math import nan, inf
import pytest

from stock_harness.trend_risk_reward import SCENARIO_VERSION, evaluate_trend_space, grade_ratio


def scenario(target=12, **kwargs):
    return {"contract_version": SCENARIO_VERSION, "state": "triggered",
            "direction": "long", "entry_price": 10, "invalidation_price": 9,
            "targets": [{"price": target, "risk_reward_ratio": 99,
                         "stressed_risk_reward_ratio": .1}], **kwargs}


@pytest.mark.parametrize("ratio,status", [(0, "insufficient"), (1, "insufficient"),
    (1.00000001, "qualified"), (2, "qualified"), (2.00000001, "opportunity"),
    (None, "unavailable"), (nan, "unavailable"), (inf, "unavailable")])
def test_strict_raw_boundaries(ratio, status):
    assert grade_ratio(ratio) == status


@pytest.mark.parametrize("target,status", [(11, "insufficient"), (11.00000001, "qualified"),
    (12, "qualified"), (12.00000001, "opportunity")])
def test_recomputes_unrounded_prices_not_cached_ratios(target, status):
    result = evaluate_trend_space(scenario(target))
    assert result["status"] == status
    assert result["risk_distance"] == 1
    assert result["reward_distance"] == pytest.approx(target - 10)


def test_nearest_target_not_distant_target_or_stress_qualifies():
    value = scenario(10.5)
    value["targets"].append({"price": 100, "risk_reward_ratio": 90})
    assert evaluate_trend_space(value)["status"] == "insufficient"
    assert evaluate_trend_space(scenario(12.1))["opportunity"] is True


@pytest.mark.parametrize("kwargs,reason", [
    ({"contract_version": "structural-trade-scenario-v4-owned-stop"}, "legacy-analysis-recalculate"),
    ({"qualification_blocked": True}, "price-basis-unverified"),
    ({"invalidation_price": 11}, "invalid-price-ordering"),
    ({"entry_price": nan}, "invalid-price"),
    ({"targets": []}, "missing-nearest-target"),
    ({"state": "invalidated"}, "inactive-or-missing-structure"),
])
def test_fail_closed_reasons_are_distinct(kwargs, reason):
    result = evaluate_trend_space(scenario(**kwargs))
    assert result["status"] == "unavailable"
    assert result["reason"] == reason
    assert not result["opportunity"]


def test_short_geometry_and_low_rr_are_not_malformed():
    assert evaluate_trend_space(scenario(7, direction="short", invalidation_price=11))["opportunity"]
    result = evaluate_trend_space(scenario(10.2))
    assert result["status"] == "insufficient" and result["reason"] is None


def test_decimal_price_exactly_two_is_not_promoted_by_float_noise():
    value = scenario(12.3, entry_price=10.1, invalidation_price=9)
    result = evaluate_trend_space(value)
    assert result["risk_reward_ratio"] == 2
    assert result["status"] == "qualified"


def test_unverified_prices_keep_reference_distances_but_never_qualify():
    result = evaluate_trend_space(scenario(14, qualification_blocked=True))
    assert result["reward_distance"] == 4 and result["risk_distance"] == 1
    assert result["status"] == "unavailable" and not result["qualified"]
