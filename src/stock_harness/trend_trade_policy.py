"""Pure trend-trade-v1 research rules; no detection, data access or real orders."""

from dataclasses import dataclass
from datetime import date
from math import floor, isfinite
from typing import Literal

POLICY_ID = "trend-trade-v1"
MarketState = Literal["unknown", "observe", "open", "risk-off"]


def _positive(*values: float) -> bool:
    return all(isfinite(v) and v > 0 for v in values)


@dataclass(frozen=True, slots=True)
class TrendFacts:
    close: float
    sma20: float
    sma60: float
    sma60_previous5: float
    return20: float

    def valid(self) -> bool:
        return _positive(self.close, self.sma20, self.sma60, self.sma60_previous5) and isfinite(self.return20)

    def rising(self) -> bool:
        return self.close > self.sma60 > self.sma60_previous5


@dataclass(frozen=True, slots=True)
class MarketDecision:
    session: date
    state: MarketState
    raw_pass: bool
    reasons: tuple[str, ...]


def market_permission(
    session: date, previous_session: date, facts: TrendFacts | None,
    *, breadth: float | None, coverage: float, atr20: float | None,
    previous: MarketDecision | None = None,
) -> MarketDecision:
    """The caller supplies consecutive exchange sessions, not calendar-day guesses."""
    if previous_session >= session or (previous is not None and previous.session >= session):
        raise ValueError("market decisions require strictly increasing sessions")
    if (facts is None or not facts.valid() or breadth is None or atr20 is None
            or not isfinite(breadth) or not 0 <= breadth <= 1
            or not isfinite(coverage) or not .95 <= coverage <= 1 or not _positive(atr20)):
        return MarketDecision(session, "unknown", False, ("incomplete-market-evidence",))
    if facts.close < facts.sma60 and breadth < .35:
        return MarketDecision(session, "risk-off", False, ("market-breakdown",))
    reasons = tuple(reason for failed, reason in (
        (not facts.rising() or facts.sma20 <= facts.sma60, "benchmark-trend"),
        (breadth < .55, "breadth"), (atr20 / facts.close > .03, "volatility"),
    ) if failed)
    raw_pass = not reasons
    confirmed = raw_pass and previous is not None and previous.session == previous_session and previous.raw_pass
    return MarketDecision(session, "open" if confirmed else "observe", raw_pass,
                          reasons if reasons else (() if confirmed else ("await-second-session",)))


def qualification_reasons(
    market: MarketDecision, benchmark: TrendFacts, sector: TrendFacts, stock: TrendFacts,
    *, median_amount20: float, historical_eligibility_verified: bool,
) -> tuple[str, ...]:
    """Facts must share the decision cutoff; the pending evidence adapter verifies it."""
    if not all(f.valid() for f in (benchmark, sector, stock)) or not _positive(median_amount20):
        return ("invalid-qualification-facts",)
    return tuple(reason for failed, reason in (
        (not historical_eligibility_verified, "historical-eligibility-unverified"),
        (market.state != "open", "market-not-open"),
        (not sector.rising() or sector.sma20 <= sector.sma60, "sector-trend"),
        (sector.return20 <= benchmark.return20, "sector-relative-strength"),
        (not stock.rising(), "stock-parent-trend"),
        (stock.return20 < sector.return20, "stock-relative-strength"),
        (median_amount20 < 100_000_000, "liquidity"),
    ) if failed)


@dataclass(frozen=True, slots=True)
class PricePlan:
    signal_session: date
    entry_session: date
    upper: float
    ceiling: float
    stop: float
    target: float

    def __post_init__(self) -> None:
        if (self.entry_session <= self.signal_session
                or not _positive(self.stop, self.upper, self.ceiling, self.target)
                or not self.stop < self.upper < self.ceiling < self.target):
            raise ValueError("invalid frozen price plan")

    @property
    def reward_risk(self) -> float:
        return (self.target - self.ceiling) / (self.ceiling - self.stop)


def build_price_plan(
    signal_session: date, entry_session: date, *, upper: float, lower: float,
    close: float, atr20: float, target: float,
) -> PricePlan:
    """Consume verified shared geometry and selected resistance; do not detect either."""
    if not _positive(lower, upper, close, atr20, target) or not lower < upper < close:
        raise ValueError("invalid breakout geometry")
    ceiling = 1.02 * close
    plan = PricePlan(signal_session, entry_session, upper, ceiling,
                     max(lower - .25 * atr20, upper - atr20), target)
    distance = (plan.ceiling - plan.stop) / plan.ceiling
    if not .02 <= distance <= .08 or plan.reward_risk < 2:
        raise ValueError("insufficient structural space or unsuitable stop distance")
    return plan


@dataclass(frozen=True, slots=True)
class PortfolioCapacity:
    equity: float
    cash: float
    stock_exposure: float = 0
    sector_exposure: float = 0
    gross_exposure: float = 0
    stop_risk: float = 0
    stock_count: int = 0
    symbol_reserved: bool = False


def size_entry(
    plan: PricePlan, capacity: PortfolioCapacity, *, lot_size: int,
    median_amount20: float, entry_fee_rate: float, round_trip_risk_rate: float,
) -> int:
    """Caller includes held positions AND pending reservations; never future sales."""
    numbers = (capacity.cash, capacity.stock_exposure, capacity.sector_exposure,
               capacity.gross_exposure, capacity.stop_risk, entry_fee_rate, round_trip_risk_rate)
    if (not _positive(capacity.equity, median_amount20)
            or any(not isfinite(v) or v < 0 for v in numbers)
            or type(lot_size) is not int or lot_size <= 0
            or type(capacity.stock_count) is not int or capacity.stock_count < 0
            or not entry_fee_rate <= round_trip_risk_rate < 1):
        raise ValueError("invalid sizing inputs")
    if capacity.symbol_reserved or capacity.stock_exposure > 0 or capacity.stock_count >= 5:
        return 0
    equity, price = capacity.equity, plan.ceiling
    risk = price - plan.stop + price * round_trip_risk_rate
    quantity = min(
        .005 * equity / risk,
        (.02 * equity - capacity.stop_risk) / risk,
        (.10 * equity - capacity.stock_exposure) / price,
        (.20 * equity - capacity.sector_exposure) / price,
        (.50 * equity - capacity.gross_exposure) / price,
        .001 * median_amount20 / price,
        capacity.cash / (price * (1 + entry_fee_rate)),
    )
    return max(0, floor(quantity / lot_size)) * lot_size
