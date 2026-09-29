"""Causal long-only close decisions and later opening fills for research policies.

No OHLC path inference, portfolio bookkeeping or exchange-calendar discovery.
Opening evidence must be known at the opening, never full-day volume/high/low.
"""

from dataclasses import dataclass, replace
from datetime import date
from math import isfinite

from stock_harness.trend_trade_policy import PricePlan


@dataclass(frozen=True, slots=True)
class OpeningEvidence:
    session: date
    price: float
    lower_limit: float
    upper_limit: float
    tradable: bool
    verified: bool

    def valid(self) -> bool:
        return (self.verified and self.tradable
                and all(isfinite(v) and v > 0 for v in (self.price, self.lower_limit, self.upper_limit))
                and self.lower_limit < self.upper_limit
                and self.lower_limit <= self.price <= self.upper_limit)


def entry_price(plan: PricePlan, opening: OpeningEvidence, *, slippage_rate: float) -> float | None:
    _slippage(slippage_rate)
    if (opening.session != plan.entry_session or not opening.valid()
            or not opening.lower_limit < opening.price < opening.upper_limit):
        return None
    price = opening.price * (1 + slippage_rate)
    return price if plan.upper <= price <= min(plan.ceiling, opening.upper_limit) else None


@dataclass(frozen=True, slots=True)
class Holding:
    entry_session: date
    earliest_sell_session: date
    entry: float
    initial_stop: float
    target: float
    active_stop: float
    peak_close: float | None = None
    trailing_armed: bool = False
    last_close_session: date | None = None
    exit_reason: str | None = None
    exit_trigger_session: date | None = None

    def __post_init__(self) -> None:
        if (self.earliest_sell_session <= self.entry_session
                or not all(isfinite(v) and v > 0 for v in
                           (self.entry, self.initial_stop, self.target, self.active_stop))
                or not self.initial_stop < self.entry < self.target
                or self.active_stop < self.initial_stop
                or (self.peak_close is not None and (not isfinite(self.peak_close) or self.peak_close <= 0))
                or (self.last_close_session is not None and self.last_close_session < self.entry_session)
                or (self.exit_reason is None) != (self.exit_trigger_session is None)
                or (self.exit_trigger_session is not None and (
                    self.exit_trigger_session < self.entry_session or self.last_close_session is None
                    or self.exit_trigger_session > self.last_close_session))):
            raise ValueError("invalid holding state")


def after_close(
    holding: Holding, session: date, *, close: float | None, atr20: float | None,
    market_risk_off: bool = False, account_risk: bool = False,
) -> Holding:
    if session < holding.entry_session or (holding.last_close_session is not None and session <= holding.last_close_session):
        raise ValueError("close decisions must advance monotonically")
    updated = replace(holding, last_close_session=session)
    if holding.exit_reason is not None:
        return updated
    reason = "account-risk" if account_risk else "market-risk" if market_risk_off else None
    valid_close = close is not None and isfinite(close) and close > 0
    if reason is None and valid_close:
        if close <= holding.active_stop:
            reason = "trailing-stop" if holding.active_stop > holding.initial_stop else "structural-stop"
        elif close >= holding.target:
            reason = "target"
    if reason is not None:
        return replace(updated, exit_reason=reason, exit_trigger_session=session)
    if not valid_close:
        return updated
    peak = max(holding.peak_close or close, close)
    armed = holding.trailing_armed or peak >= holding.entry + 2 * (holding.entry - holding.initial_stop)
    stop = holding.active_stop
    if armed and atr20 is not None and isfinite(atr20) and atr20 > 0:
        # Today's trigger used the old stop. This raised stop is for later sessions.
        stop = max(stop, peak - 2 * atr20)
    return replace(updated, peak_close=peak, trailing_armed=armed, active_stop=stop)


def exit_price(holding: Holding, opening: OpeningEvidence, *, slippage_rate: float) -> float | None:
    _slippage(slippage_rate)
    if (holding.exit_trigger_session is None
            or opening.session <= holding.exit_trigger_session
            or opening.session < holding.earliest_sell_session
            or (holding.last_close_session is not None and opening.session <= holding.last_close_session)
            or not opening.valid() or opening.price <= opening.lower_limit):
        return None
    price = opening.price * (1 - slippage_rate)
    return price if price >= opening.lower_limit else None


def _slippage(value: float) -> None:
    if not isfinite(value) or not 0 <= value < 1:
        raise ValueError("slippage must be finite and between zero and one")
