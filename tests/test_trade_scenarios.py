from stock_harness.trade_scenarios import (
    TRADE_SCENARIO_VERSION,
    TradeDirection,
    TradeTargetSide,
    build_trade_scenario,
    calculate_risk_reward,
)


def test_shared_risk_reward_supports_long_and_short_scenarios() -> None:
    assert calculate_risk_reward(TradeDirection.LONG, 10, 9, 12) == 2
    assert calculate_risk_reward(TradeDirection.SHORT, 10, 11, 8) == 2


def test_shared_risk_reward_rejects_invalid_ordering_and_prices() -> None:
    assert calculate_risk_reward(TradeDirection.LONG, 10, 11, 12) is None
    assert calculate_risk_reward(TradeDirection.SHORT, 10, 9, 8) is None
    assert calculate_risk_reward(TradeDirection.LONG, 0, 9, 12) is None


def test_versioned_trade_scenario_owns_targets_assumptions_and_evidence() -> None:
    scenario = build_trade_scenario(
        method="test-method",
        direction=TradeDirection.LONG,
        entry_price=10,
        invalidation_price=9,
        targets=(
            (12, "range-high", TradeTargetSide.UPSIDE),
            (8, "range-low", TradeTargetSide.DOWNSIDE),
        ),
        setup_basis="test-setup",
        assumptions=("daily bars only",),
        evidence_item_ids=("long-line-1",),
    )

    payload = scenario.to_payload()
    assert payload["contract_version"] == TRADE_SCENARIO_VERSION
    assert payload["has_trade_space"] is True
    assert payload["assumptions"] == ["daily bars only"]
    assert payload["evidence_item_ids"] == ["long-line-1"]
    assert payload["targets"] == [
        {
            "price": 12,
            "basis": "range-high",
            "side": "upside",
            "risk_reward_ratio": 2,
        },
        {
            "price": 8,
            "basis": "range-low",
            "side": "downside",
            "risk_reward_ratio": None,
        },
    ]
