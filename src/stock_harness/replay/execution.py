"""Daily-bar exit execution shared by all systems, with frozen trade outcomes."""

from collections import Counter
from collections.abc import Mapping, Sequence
from statistics import fmean, median

from stock_harness.models import StoredDailyBar
from stock_harness.replay.contracts import FrozenSignal


def evaluate_trade(
    signal: FrozenSignal, bars: Sequence[StoredDailyBar], entry: float,
) -> dict[str, object]:
    plan = signal.resolved_exit_plan()
    if plan is None:
        return {"status": "not-configured", "reason": "missing-exit-plan"}
    plan.validate()
    sign = 1 if signal.direction == "long" else -1
    stop, target = plan.stop_price, plan.target_price
    if not (sign * (entry - stop) > 0 and sign * (target - entry) > 0):
        return {"status": "not-entered", "reason": "entry-outside-frozen-boundaries"}
    for session, bar in enumerate(bars, 1):
        stop_gap = sign * (bar.open - stop) <= 0
        target_gap = sign * (bar.open - target) >= 0
        stop_hit = bar.low <= stop if sign == 1 else bar.high >= stop
        target_hit = bar.high >= target if sign == 1 else bar.low <= target
        ambiguous = False
        if stop_gap or target_gap:
            price = float(bar.open)
            reason = "stop-loss-gap" if stop_gap else "take-profit-gap"
        elif stop_hit or target_hit:
            ambiguous = stop_hit and target_hit
            price = stop if stop_hit else target
            reason = "stop-loss" if stop_hit else "take-profit"
        elif plan.maximum_holding_sessions == session:
            price, reason = float(bar.close), "time-exit"
        else:
            continue
        gross = sign * (price / entry - 1)
        net = gross - plan.round_trip_cost_bps / 10000
        return {
            "status": "closed", "policy_id": plan.policy_id,
            "entry_price": entry, "exit_date": bar.trade_date.isoformat(),
            "exit_price": price, "exit_reason": reason, "holding_sessions": session,
            "gross_return": round(gross, 6), "net_return": round(net, 6),
            "net_r_multiple": round(net * entry / abs(entry - stop), 6),
            "same_session_ambiguous": ambiguous,
            "execution_model": "daily-ohlc-stop-first-ideal-fill-v1",
            "round_trip_cost_bps": plan.round_trip_cost_bps,
        }
    return {
        "status": "open-right-censored", "policy_id": plan.policy_id,
        "observed_sessions": len(bars), "net_return": None,
    }


def summarize_trades(values: Sequence[Mapping[str, object]]) -> dict[str, object]:
    closed = [value for value in values if value.get("status") == "closed"]
    returns = [float(value["net_return"]) for value in closed]
    profits = sum(value for value in returns if value > 0)
    losses = -sum(value for value in returns if value < 0)
    return {
        "status_counts": dict(Counter(str(v.get("status")) for v in values)),
        "closed_count": len(closed),
        "win_rate": sum(v > 0 for v in returns) / len(returns) if returns else None,
        "mean_net_return": fmean(returns) if returns else None,
        "median_net_return": median(returns) if returns else None,
        "profit_factor": profits / losses if losses else None,
        "mean_net_r_multiple": fmean(float(v["net_r_multiple"]) for v in closed) if closed else None,
        "mean_holding_sessions": fmean(int(v["holding_sessions"]) for v in closed) if closed else None,
        "ambiguous_count": sum(bool(v["same_session_ambiguous"]) for v in closed),
        "exit_reasons": dict(Counter(str(v["exit_reason"]) for v in closed)),
    }
