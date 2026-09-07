from stock_harness.trade_scenarios import TradeDirection, calculate_risk_reward


def test_shared_risk_reward_supports_long_and_short_scenarios() -> None:
    assert calculate_risk_reward(TradeDirection.LONG, 10, 9, 12) == 2
    assert calculate_risk_reward(TradeDirection.SHORT, 10, 11, 8) == 2


def test_shared_risk_reward_rejects_invalid_ordering_and_prices() -> None:
    assert calculate_risk_reward(TradeDirection.LONG, 10, 11, 12) is None
    assert calculate_risk_reward(TradeDirection.SHORT, 10, 9, 8) is None
    assert calculate_risk_reward(TradeDirection.LONG, 0, 9, 12) is None
