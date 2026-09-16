"""Offline frozen-policy diagnostics; never replaces shared pattern detection."""

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from math import isfinite
from statistics import fmean, median

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.low_base_pullback import ALGORITHM_VERSION, CONFIG, detect_low_base_pullback


@dataclass(frozen=True, slots=True)
class ValidationPolicy:
    signal_start: str = "2026-01-01"
    signal_end: str = "2026-08-31"
    outcome_end: str = "2026-09-15"
    slippage_bps: float = 5.
    fee_bps_per_side: float = 10.
    trailing_activation: float = .15
    trailing_drawdown: float = .10
    confirmation_stop_allowance: float = .02


POLICY = ValidationPolicy()


def analysis_bars(rows):
    bars = []
    for row in rows:
        day = date.fromisoformat(row["trade_date"])
        bars.append(AnalysisBar(
            period_start=day, period_end=day, open=row["open"], high=row["high"],
            low=row["low"], close=row["close"], volume=row["volume"],
            sources=(row.get("source", "unknown"),), contains_provisional=row.get("bar_state") != "final",
            period_complete=row.get("bar_state") == "final", observed_at_ms=0,
        ))
    if any(a.period_end >= b.period_end for a, b in zip(bars, bars[1:])):
        raise ValueError("snapshot dates must be strictly increasing; never sort away input defects")
    return tuple(bars)


def _executable(bar):
    return (bar.period_complete and not bar.contains_provisional and bar.volume > 0
            and all(isfinite(v) and v > 0 for v in (bar.open, bar.high, bar.low, bar.close))
            and bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high
            and bar.high > bar.low)


def evaluate_episode(bars, signal_index, evidence, policy=POLICY):
    """Close-triggered stops execute at a later available open, never the stop price."""
    if not 0 <= signal_index < len(bars):
        raise ValueError("signal index outside history")
    signal = bars[signal_index]
    if evidence["as_of_date"] != signal.period_end.isoformat():
        raise ValueError("evidence date does not match signal")
    stop = float(evidence["invalidation_price"])
    if evidence["stage"] == "pullback-confirmed":
        stop = max(stop, evidence["breakout_price"] * (1 - policy.confirmation_stop_allowance))
    record = {
        "signal_date": signal.period_end.isoformat(), "launch_date": evidence["launch_date"],
        "stage": evidence["stage"], "score": evidence["score"], "initial_stop": stop,
        "status": "no-next-bar", "entry_date": None, "exit_date": None,
        "exit_reason": None, "exit_trigger_date": None, "exit_delayed_sessions": 0,
        "net_return_percent": None, "mark_return_percent": None,
        "max_close_gain_percent": None, "max_adverse_close_percent": None,
        "reached_20_percent": False, "reached_100_percent": False,
        "raw_discontinuity_dates": [],
        "origin_above_context_low_percent": evidence.get("origin_above_context_low_percent"),
    }
    future = [b for b in bars[signal_index + 1:] if b.period_end.isoformat() <= policy.outcome_end]
    if not future:
        return record
    entry_bar = future[0]
    if not _executable(entry_bar):
        record["status"] = "entry-unavailable"
        return record
    if entry_bar.open <= stop:
        record["status"] = "entry-below-stop"
        return record
    slip = policy.slippage_bps / 10000
    fee = policy.fee_bps_per_side / 10000
    entry = entry_bar.open * (1 + slip)
    record.update(status="open-censored", entry_date=entry_bar.period_end.isoformat(), entry_price=entry,
                  entry_gap_calendar_days=(entry_bar.period_end - signal.period_end).days)
    peak, trough = entry, entry
    pending = None
    previous_close = signal.close
    # Past price gaps are warnings, never retroactive grounds to discard losers.
    history = bars[max(0, signal_index - 249):signal_index + 1]
    for previous, current in zip(history, history[1:]):
        if abs(current.close / previous.close - 1) > .20:
            record["raw_discontinuity_dates"].append(current.period_end.isoformat())
    for offset, bar in enumerate(future):
        day = bar.period_end.isoformat()
        if pending is not None and offset > 0 and _executable(bar):
            exit_price = bar.open * (1 - slip)
            record.update(status="closed", exit_date=day, exit_price=exit_price,
                          exit_reason=pending, net_return_percent=round(
                              (exit_price * (1 - fee) / (entry * (1 + fee)) - 1) * 100, 4))
            # The exit session's CLOSE is unavailable at its opening fill.
            if abs(bar.open / previous_close - 1) > .20:
                record["raw_discontinuity_dates"].append(day)
            break
        if abs(bar.close / previous_close - 1) > .20:
            record["raw_discontinuity_dates"].append(day)
        previous_close = bar.close
        peak, trough = max(peak, bar.close), min(trough, bar.close)
        active_stop = stop
        trailing = peak >= entry * (1 + policy.trailing_activation)
        if trailing:
            active_stop = max(stop, peak * (1 - policy.trailing_drawdown))
        if pending is not None:
            record["exit_delayed_sessions"] += 1
        elif bar.close < active_stop:
            pending = "structural-stop" if bar.close < stop else "trailing-profit-stop"
            record["exit_trigger_date"] = day
    record["max_close_gain_percent"] = round((peak / entry - 1) * 100, 4)
    record["max_adverse_close_percent"] = round((trough / entry - 1) * 100, 4)
    record["reached_20_percent"] = peak >= entry * 1.20
    record["reached_100_percent"] = peak >= entry * 2
    if record["status"] != "closed":
        record["status"] = "exit-pending-censored" if pending is not None else "open-censored"
        record["exit_reason"] = pending
        record["mark_date"] = future[-1].period_end.isoformat()
        record["mark_return_percent"] = round((future[-1].close / entry - 1) * 100, 4)
    return record


def summarize(episodes):
    entered = [e for e in episodes if e["entry_date"]]
    closed = [e for e in entered if e["status"] == "closed"]
    def med(field, selected):
        values = [e[field] for e in selected if e[field] is not None]
        return round(median(values), 4) if values else None
    return {
        "episodes": len(episodes), "entered": len(entered), "closed": len(closed),
        "statuses": dict(Counter(e["status"] for e in episodes)),
        "closed_positive": sum(e["net_return_percent"] > 0 for e in closed),
        "closed_nonpositive": sum(e["net_return_percent"] <= 0 for e in closed),
        "closed_positive_fraction": sum(e["net_return_percent"] > 0 for e in closed) / len(closed) if closed else None,
        "median_closed_net_return_percent": med("net_return_percent", closed),
        "mean_closed_net_return_percent": round(fmean(e["net_return_percent"] for e in closed), 4) if closed else None,
        "sum_positive_closed_percent": round(sum(max(0., e["net_return_percent"]) for e in closed), 4),
        "sum_negative_closed_percent": round(sum(min(0., e["net_return_percent"]) for e in closed), 4),
        "median_max_adverse_close_percent": med("max_adverse_close_percent", entered),
        "worst_adverse_close_percent": min((e["max_adverse_close_percent"] for e in entered), default=None),
        "median_max_close_gain_percent": med("max_close_gain_percent", entered),
        "reached_20_percent": sum(e["reached_20_percent"] for e in entered),
        "reached_100_percent": sum(e["reached_100_percent"] for e in entered),
        "raw_discontinuity_flagged": sum(bool(e["raw_discontinuity_dates"]) for e in entered),
    }


def audit_features(prefix, evidence):
    """Post-hoc descriptive diagnostics, never additional eligibility gates."""
    platform = [b for b in prefix if b.period_end.isoformat() >= evidence["start_date"]]
    up = [b.volume for b in platform if b.close > b.open]
    down = [b.volume for b in platform if b.close < b.open]
    ma60 = fmean(b.close for b in prefix[-60:])
    old_ma60 = fmean(b.close for b in prefix[-70:-10]) if len(prefix) >= 70 else None
    return {
        "below_ma60": prefix[-1].close < ma60,
        "ma60_ten_session_change_percent": round((ma60 / old_ma60 - 1) * 100, 4) if old_ma60 else None,
        "platform_up_down_mean_volume_ratio": round(fmean(up) / fmean(down), 4) if up and down else None,
        "platform_close_change_percent": round((platform[-1].close / platform[0].close - 1) * 100, 4) if platform else None,
    }


def replay_snapshot(snapshot, policy=POLICY):
    if not policy.signal_start <= policy.signal_end <= policy.outcome_end:
        raise ValueError("invalid validation date ordering")
    episodes, coverage = [], []
    screened_days = Counter()
    signals = []
    for item in snapshot["instruments"]:
        symbol = item["symbol"]
        bars = analysis_bars([r for r in item.get("bars", []) if r["trade_date"] <= policy.outcome_end])
        seen = set()
        dates = [b.period_end.isoformat() for b in bars]
        coverage.append({"symbol": symbol, "name": item.get("name"), "bars": len(bars),
                         "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None,
                         "signal_sessions": sum(policy.signal_start <= d <= policy.signal_end for d in dates),
                         "error": item.get("error"), "truncated": item.get("truncated", False)})
        for index, day in enumerate(dates):
            if not policy.signal_start <= day <= policy.signal_end:
                continue
            result = detect_low_base_pullback(bars[:index + 1])
            if result is None or not result["screen_eligible"]:
                continue
            screened_days[result["stage"]] += 1
            key = (result["launch_date"], result["stage"])
            if key in seen:
                continue
            seen.add(key)
            signals.append({"symbol": symbol, "evidence": result,
                            "audit_features": audit_features(bars[:index + 1], result)})
            episodes.append({"symbol": symbol, "name": item.get("name"),
                             **evaluate_episode(bars, index, result, policy)})
    episodes.sort(key=lambda e: (e["signal_date"], e["symbol"], e["stage"]))
    stages = ("pullback-observation", "pullback-confirmed")
    confirmed_keys = {(e["symbol"], e["launch_date"]) for e in episodes if e["stage"] == stages[1]}
    observations = [e for e in episodes if e["stage"] == stages[0]]
    return {
        "algorithm_version": ALGORITHM_VERSION, "detector_parameters": asdict(CONFIG),
        "policy": asdict(policy), "coverage": coverage,
        "screened_symbol_days": dict(screened_days), "unique_seeds": len({(e["symbol"], e["launch_date"]) for e in episodes}),
        "observed_seeds_confirmed_in_signal_window": sum((e["symbol"], e["launch_date"]) in confirmed_keys for e in observations),
        "by_stage": {stage: summarize([e for e in episodes if e["stage"] == stage]) for stage in stages},
        "by_period": {label: {stage: summarize([e for e in episodes if e["stage"] == stage
                                               and start <= e["signal_date"] <= end]) for stage in stages}
                      for label, start, end in (("Jan-Apr", "2026-01-01", "2026-04-30"),
                                                 ("May-Aug", "2026-05-01", "2026-08-31"))},
        "signals": signals, "episodes": episodes,
        "limitations": ["raw-unadjusted-prices", "current-catalog-not-point-in-time-universe",
                        "prefix-stratified-not-market-representative", "no-verified-limit-prices-or-fill-depth",
                        "one-price-entry-skipped-exit-deferred", "independent-overlapping-episodes-not-portfolio",
                        "open-trades-censored-not-losses-or-wins", "calibrated-detector-frozen-before-cohort-outcomes"],
    }
