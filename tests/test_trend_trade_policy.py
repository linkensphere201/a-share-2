from dataclasses import replace
from datetime import date, timedelta

import pytest

from stock_harness.trend_trade_policy import (
    MarketDecision, PortfolioCapacity, TrendFacts, build_price_plan,
    market_permission, qualification_reasons, size_entry,
)
from stock_harness.replay.close_execution import (
    Holding, OpeningEvidence, after_close, entry_price, exit_price,
)

DAY = date(2026, 9, 25)
NEXT = date(2026, 9, 28)
FACTS = TrendFacts(110, 108, 100, 99, .05)


def market(day=DAY, previous_day=DAY - timedelta(days=1), **kwargs):
    values = dict(breadth=.60, coverage=1., atr20=2.)
    values.update(kwargs)
    return market_permission(day, previous_day, FACTS, **values)


def plan():
    return build_price_plan(DAY, NEXT, upper=10., lower=9.5, close=10.2, atr20=.4, target=12.5)


def holding():
    return Holding(NEXT, NEXT + timedelta(days=1), 10., 9., 16., 9.)


def opening(day=NEXT, price=10.25, **kwargs):
    values = dict(lower_limit=9., upper_limit=12., tradable=True, verified=True)
    values.update(kwargs)
    return OpeningEvidence(day, price, **values)


def test_market_confirmation_uses_exchange_adjacency_not_calendar_days():
    first = market()
    assert first.state == 'observe' and first.raw_pass
    assert market(NEXT, DAY, previous=first).state == 'open'
    assert market(NEXT, DAY - timedelta(days=1), previous=first).state == 'observe'


@pytest.mark.parametrize('changes', [dict(breadth=None), dict(breadth=float('nan')),
                                   dict(coverage=.94), dict(coverage=1.1), dict(atr20=0)])
def test_incomplete_market_blocks_entries(changes):
    assert market(**changes).state == 'unknown'


def test_risk_off_and_unknown_reset_confirmation():
    risk = market_permission(DAY, DAY - timedelta(days=1), replace(FACTS, close=90),
                             breadth=.3, coverage=1., atr20=2.)
    assert risk.state == 'risk-off' and not risk.raw_pass
    assert market(NEXT, DAY, previous=risk).state == 'observe'
    assert market(NEXT, DAY, previous=market(coverage=.9)).state == 'observe'


def test_qualification_never_uses_current_tags_or_missing_history():
    state = MarketDecision(DAY, 'open', True, ())
    args = (state, FACTS, replace(FACTS, return20=.08), replace(FACTS, return20=.10))
    assert qualification_reasons(*args, median_amount20=1e8, historical_eligibility_verified=True) == ()
    assert qualification_reasons(*args, median_amount20=1e8, historical_eligibility_verified=False) == (
        'historical-eligibility-unverified',)
    bad = qualification_reasons(state, FACTS, FACTS, replace(FACTS, close=90),
                                median_amount20=5e7, historical_eligibility_verified=True)
    assert set(bad) == {'sector-relative-strength', 'stock-parent-trend', 'liquidity'}


def test_plan_freezes_worst_entry_and_structural_stop():
    value = plan()
    assert value.ceiling == pytest.approx(10.404)
    assert value.stop == pytest.approx(9.6)
    assert value.reward_risk >= 2


@pytest.mark.parametrize('changes', [dict(target=11), dict(atr20=2), dict(lower=11),
                                   dict(close=float('nan')), dict(entry_session=DAY)])
def test_invalid_or_poor_space_plan_rejected(changes):
    values = dict(signal_session=DAY, entry_session=NEXT, upper=10., lower=9.5,
                  close=10.2, atr20=.4, target=12.5)
    values.update(changes)
    with pytest.raises(ValueError):
        build_price_plan(**values)


def size(capacity, **changes):
    values = dict(lot_size=100, median_amount20=1e8, entry_fee_rate=.001,
                  round_trip_risk_rate=.003)
    values.update(changes)
    return size_entry(plan(), capacity, **values)


def test_size_obeys_cash_risk_and_round_lots():
    capacity = PortfolioCapacity(100_000, 100_000)
    quantity = size(capacity)
    assert quantity == 500
    assert quantity * (plan().ceiling - plan().stop + plan().ceiling * .003) <= 500
    assert size(replace(capacity, cash=1000)) == 0
    assert size(replace(capacity, cash=2100)) == 200


@pytest.mark.parametrize('changes', [dict(stock_count=5), dict(symbol_reserved=True),
                                   dict(stock_exposure=100), dict(sector_exposure=20_000),
                                   dict(gross_exposure=50_000), dict(stop_risk=2000)])
def test_portfolio_caps_and_duplicate_reservations(changes):
    assert size(replace(PortfolioCapacity(100_000, 100_000), **changes)) == 0


def test_invalid_sizes_fail_not_silently_round():
    with pytest.raises(ValueError):
        size(PortfolioCapacity(100_000, -1))
    with pytest.raises(ValueError):
        size(PortfolioCapacity(100_000, 100_000), lot_size=0)


def test_opening_entry_is_next_session_only_and_never_chases():
    assert entry_price(plan(), opening(), slippage_rate=.0005) == pytest.approx(10.255125)
    assert entry_price(plan(), opening(DAY), slippage_rate=0) is None
    assert entry_price(plan(), opening(NEXT + timedelta(days=1)), slippage_rate=0) is None
    assert entry_price(plan(), opening(price=10.5), slippage_rate=0) is None
    assert entry_price(plan(), opening(price=9.9), slippage_rate=0) is None


@pytest.mark.parametrize('changes', [dict(price=9), dict(price=12), dict(verified=False),
                                   dict(tradable=False), dict(lower_limit=float('nan'))])
def test_opening_limits_and_unverified_data_prevent_entry(changes):
    assert entry_price(plan(), opening(**changes), slippage_rate=0) is None


def test_close_stop_cannot_fill_entry_day_and_gap_is_not_stop_price():
    state = after_close(holding(), NEXT, close=8.9, atr20=.5)
    assert state.exit_reason == 'structural-stop'
    assert exit_price(state, opening(price=9), slippage_rate=0) is None
    tomorrow = NEXT + timedelta(days=1)
    assert exit_price(state, opening(tomorrow, price=8, lower_limit=7), slippage_rate=.001) == pytest.approx(7.992)


def test_limit_down_defers_exit_and_rebound_does_not_cancel():
    state = after_close(holding(), NEXT, close=8.9, atr20=.5)
    tomorrow = NEXT + timedelta(days=1)
    assert exit_price(state, opening(tomorrow, price=9), slippage_rate=0) is None
    state = after_close(state, tomorrow, close=11, atr20=.5)
    assert state.exit_reason == 'structural-stop'
    assert state.exit_trigger_session == NEXT
    assert exit_price(state, opening(tomorrow + timedelta(days=1)), slippage_rate=0) is not None


def test_raised_trailing_stop_only_applies_to_later_close():
    state = after_close(holding(), NEXT, close=13, atr20=2)
    assert state.trailing_armed and state.active_stop == 9
    tomorrow = NEXT + timedelta(days=1)
    state = after_close(state, tomorrow, close=11, atr20=.5)
    assert state.exit_reason is None and state.active_stop == 12
    state = after_close(state, tomorrow + timedelta(days=1), close=11.5, atr20=1)
    assert state.exit_reason == 'trailing-stop'


def test_trailing_never_loosens_and_no_fixed_horizon_exit():
    state = after_close(holding(), NEXT, close=13, atr20=.5)
    assert state.active_stop == 12
    state = after_close(state, NEXT + timedelta(days=1), close=13, atr20=2)
    assert state.active_stop == 12 and state.exit_reason is None
    state = holding()
    for i in range(130):
        state = after_close(state, NEXT + timedelta(days=i), close=10, atr20=.5)
    assert state.exit_reason is None


@pytest.mark.parametrize('account,market_risk,expected', [
    (True, True, 'account-risk'), (False, True, 'market-risk'), (False, False, 'target'),
])
def test_exit_priority(account, market_risk, expected):
    state = after_close(holding(), NEXT, close=17, atr20=.5,
                        account_risk=account, market_risk_off=market_risk)
    assert state.exit_reason == expected


def test_missing_close_does_not_invent_price_trigger_but_risk_exit_survives():
    assert after_close(holding(), NEXT, close=None, atr20=None).active_stop == 9
    assert after_close(holding(), NEXT, close=None, atr20=None).exit_reason is None
    assert after_close(holding(), NEXT, close=None, atr20=None, account_risk=True).exit_reason == 'account-risk'


def test_cannot_use_close_information_to_fill_same_open_or_revisit_close():
    state = after_close(holding(), NEXT, close=17, atr20=.5)
    assert exit_price(state, opening(), slippage_rate=0) is None
    with pytest.raises(ValueError):
        after_close(state, NEXT, close=18, atr20=.5)


def test_future_updates_leave_frozen_prefix_unchanged():
    state = after_close(holding(), NEXT, close=10.5, atr20=.5)
    poison = after_close(state, NEXT + timedelta(days=1), close=1, atr20=.5)
    assert poison.exit_reason == 'structural-stop'
    assert state.exit_reason is None and state.active_stop == 9


def test_kernel_flow_from_environment_to_target_exit():
    state = market(NEXT, DAY, previous=market())
    sector, stock = replace(FACTS, return20=.08), replace(FACTS, return20=.09)
    assert not qualification_reasons(state, FACTS, sector, stock,
                                    median_amount20=1e8, historical_eligibility_verified=True)
    entry_day = NEXT + timedelta(days=1)
    frozen = build_price_plan(NEXT, entry_day, upper=10, lower=9.5,
                              close=10.2, atr20=.4, target=12.5)
    quantity = size_entry(frozen, PortfolioCapacity(100_000, 100_000), lot_size=100,
                          median_amount20=1e8, entry_fee_rate=.001, round_trip_risk_rate=.003)
    price = entry_price(frozen, opening(entry_day), slippage_rate=.0005)
    assert price is not None and quantity == 500
    position = Holding(entry_day, entry_day + timedelta(days=1), price,
                       frozen.stop, frozen.target, frozen.stop)
    position = after_close(position, entry_day, close=12.5, atr20=.4)
    assert position.exit_reason == 'target'
    assert exit_price(position, opening(entry_day, price=11), slippage_rate=0) is None
    assert exit_price(position, opening(entry_day + timedelta(days=1), price=11),
                      slippage_rate=.0005) == pytest.approx(10.9945)


def test_liquidity_and_fee_budget_are_enforced():
    account = PortfolioCapacity(1_000_000, 1_000_000)
    assert size(account, median_amount20=1_000_000) == 0
    account = PortfolioCapacity(100_000, plan().ceiling * 200)
    assert size(account, entry_fee_rate=0) == 200
    assert size(account) == 100


@pytest.mark.parametrize('value', [-.01, 1, float('nan'), float('inf')])
def test_invalid_slippage_rejected(value):
    with pytest.raises(ValueError):
        entry_price(plan(), opening(), slippage_rate=value)
    with pytest.raises(ValueError):
        exit_price(holding(), opening(), slippage_rate=value)
