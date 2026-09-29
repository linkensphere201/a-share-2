"""Bounded, stateless single-position scenario lab. Never a historical backtest."""

from dataclasses import asdict
from datetime import date
from hashlib import sha256
import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stock_harness.replay.close_execution import Holding, OpeningEvidence, after_close, entry_price, exit_price
from stock_harness.trend_trade_policy import POLICY_ID, PortfolioCapacity, build_price_plan, size_entry

Positive = Annotated[float, Field(gt=0, le=1e15, allow_inf_nan=False)]


class SessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session: date
    open: Positive
    close: Positive | None
    atr20: Positive | None
    lower_limit: Positive
    upper_limit: Positive
    tradable: bool = True
    verified: bool = True
    market_risk_off: bool = False

    @model_validator(mode="after")
    def check_limits(self):
        if not self.lower_limit < self.upper_limit or not self.lower_limit <= self.open <= self.upper_limit:
            raise ValueError("opening price must lie inside ordered price limits")
        if self.close is not None and not self.lower_limit <= self.close <= self.upper_limit:
            raise ValueError("closing price must lie inside price limits")
        return self


class SimulationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(default="Manual scenario", min_length=1, max_length=80)
    signal_session: date
    capital: Positive = 100_000
    upper: Positive
    lower: Positive
    signal_close: Positive
    atr20: Positive
    target: Positive
    median_amount20: Positive = 100_000_000
    lot_size: int = Field(default=100, ge=1, le=10000, strict=True)
    fee_bps: float = Field(default=10, ge=0, le=100, allow_inf_nan=False)
    slippage_bps: float = Field(default=5, ge=0, le=100, allow_inf_nan=False)
    eligibility_assumed: bool = False
    sessions: list[SessionInput] = Field(min_length=2, max_length=250)

    @model_validator(mode="after")
    def check_sessions(self):
        dates = [self.signal_session, *(s.session for s in self.sessions)]
        if any(a >= b for a, b in zip(dates, dates[1:])):
            raise ValueError("declared sessions must be strictly increasing after the signal")
        return self


def simulate(payload: SimulationInput) -> dict[str, object]:
    """Uses user-declared sessions/eligibility, not unverified application data."""
    snapshot = payload.model_dump(mode="json")
    digest = sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    plan = build_price_plan(payload.signal_session, payload.sessions[0].session,
                            upper=payload.upper, lower=payload.lower, close=payload.signal_close,
                            atr20=payload.atr20, target=payload.target)
    fee, slip = payload.fee_bps / 10000, payload.slippage_bps / 10000
    quantity = size_entry(plan, PortfolioCapacity(payload.capital, payload.capital),
                          lot_size=payload.lot_size, median_amount20=payload.median_amount20,
                          entry_fee_rate=fee, round_trip_risk_rate=2 * (fee + slip))
    cash, costs, realized = payload.capital, 0., None
    position = None
    units = 0
    bought_total = 0.
    last_mark = None
    status = "not-entered"
    events, equity = [], []

    def event(day, kind, reason, price=None):
        events.append({"session": day.isoformat(), "kind": kind, "reason": reason,
                       "price": price, "quantity": quantity if kind in {"entry", "exit"} else None})

    if not payload.eligibility_assumed:
        event(payload.signal_session, "rejected", "eligibility-not-assumed")
    elif payload.median_amount20 < 100_000_000:
        event(payload.signal_session, "rejected", "liquidity")
    elif quantity == 0:
        event(payload.signal_session, "rejected", "insufficient-capacity")

    for index, row in enumerate(payload.sessions):
        opening = OpeningEvidence(row.session, row.open, row.lower_limit, row.upper_limit,
                                  row.tradable, row.verified)
        if index == 0 and not events:
            fill = entry_price(plan, opening, slippage_rate=slip)
            if fill is None:
                event(row.session, "expired", "opening-unavailable-or-outside-plan")
                status = "expired"
            else:
                units = quantity
                bought_total = fill * units * (1 + fee)
                cash -= bought_total
                costs += fill * units * fee
                last_mark = fill
                position = Holding(row.session, payload.sessions[1].session,
                                   fill, plan.stop, plan.target, plan.stop)
                status = "held"
                event(row.session, "entry", "next-open", fill)
        elif position is not None and units and position.exit_reason:
            fill = exit_price(position, opening, slippage_rate=slip)
            if fill is None:
                event(row.session, "deferred", "exit-not-executable")
            else:
                proceeds = fill * units * (1 - fee)
                cash += proceeds
                costs += fill * units * fee
                realized = proceeds - bought_total
                units = 0
                status = "closed"
                event(row.session, "exit", position.exit_reason, fill)
        stale = False
        if position is not None and units:
            previous = position
            position = after_close(position, row.session, close=row.close, atr20=row.atr20,
                                   market_risk_off=row.market_risk_off)
            if row.close is None:
                stale = True
                event(row.session, "data-warning", "missing-close")
            else:
                last_mark = row.close
            if position.exit_reason and previous.exit_reason is None:
                event(row.session, "exit-signal", position.exit_reason)
                status = "exit-pending"
            elif position.active_stop != previous.active_stop:
                event(row.session, "stop-raised", "next-session-stop", position.active_stop)
        equity.append({"session": row.session.isoformat(), "equity": round(cash + units * (last_mark or 0), 4),
                       "cash": round(cash, 4), "quantity": units, "stale": stale,
                       "stop": position.active_stop if units and position else None})
    high, drawdown = payload.capital, 0.
    for point in equity:
        high = max(high, point["equity"])
        drawdown = max(drawdown, 1 - point["equity"] / high)
    return {
        "strategy_id": POLICY_ID, "engine_version": "single-position-scenario-v1",
        "mode": "manual-scenario-not-historical-backtest", "input_digest": digest,
        "input": snapshot, "plan": asdict(plan), "status": status,
        "planned_quantity": quantity, "events": events, "equity": equity,
        "summary": {"final_equity": equity[-1]["equity"], "net_return": equity[-1]["equity"] / payload.capital - 1,
                    "realized_pnl": realized, "fees": round(costs, 4), "max_drawdown": drawdown,
                    "open_quantity": units, "stale_valuation": equity[-1]["stale"],
                    "has_stale_valuation": any(point["stale"] for point in equity)},
        "limitations": ["manual-eligibility-and-calendar", "no-shape-or-market-verification",
                        "single-position-only", "no-corporate-actions-or-account-pause",
                        "hypothetical-fees-and-opening-fills", "not-profitability-evidence"],
    }


def example_scenarios() -> list[dict[str, object]]:
    base = dict(label="合成情景：目标退出", signal_session="2000-01-03", capital=100000,
                upper=10, lower=9.5, signal_close=10.2, atr20=.4, target=12.5,
                median_amount20=100000000, lot_size=100, fee_bps=10, slippage_bps=5,
                eligibility_assumed=True)

    def row(day, opening, close, low=7., high=15.):
        return dict(session=f"2000-01-{day:02d}", open=opening, close=close, atr20=.4,
                    lower_limit=low, upper_limit=high, tradable=True, verified=True, market_risk_off=False)

    return [
        {**base, "sessions": [row(4, 10.25, 10.5), row(5, 10.6, 12.6), row(6, 12.4, 12.3)]},
        {**base, "label": "合成情景：止损与跌停延迟", "sessions": [
            row(4, 10.25, 9.5), row(5, 9., 9., 9.), row(6, 8.5, 8.6, 8.)]},
        {**base, "label": "合成情景：跳空放弃", "sessions": [row(4, 11, 11.1), row(5, 11.1, 11.2)]},
    ]
