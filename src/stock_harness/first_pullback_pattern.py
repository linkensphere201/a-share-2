"""Causal daily first-pullback lifecycle owned by pattern analysis, not screening."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from statistics import fmean
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar


ALGORITHM_VERSION = "strong-first-pullback-v3"
KIND = "first-pullback-range"
SCREENABLE_STATES = ("pullback-observation", "pullback-confirmed")
LOOKBACK = 250


@dataclass(frozen=True, slots=True)
class FirstPullbackConfig:
    observation_window_sessions: int = 20
    min_flag_sessions: int = 2
    max_flag_range: float = .25
    max_flag_rail_rise_per_session: float = .005
    max_flag_range_expansion: float = 1.25
    base_sessions: int = 20
    max_base_range: float = .18
    breakout_buffer: float = .02
    breakout_volume_ratio: float = 1.5
    min_impulse_gain: float = .08
    max_impulse_gain: float = .45
    max_launch_sessions: int = 20
    min_pullback_fraction: float = .03
    max_retracement: float = .60
    max_pullback_sessions: int = 12
    max_pullback_volume_ratio: float = .85
    confirmation_fresh_sessions: int = 2
    momentum_daily_gain: float = .07
    momentum_min_gain: float = .15
    momentum_max_gain: float = .80
    momentum_max_retracement: float = .75
    momentum_max_turnover_ratio: float = 1.


CONFIG = FirstPullbackConfig()


def detect_first_pullback(bars: Sequence[AnalysisBar]) -> dict[str, object] | None:
    """Use only the supplied final daily prefix; never rearm inside an old leg.

    A base launch requires a fresh base; a strong impulse may restart after failure.
    Thresholds are provisional research defaults, not calibrated trade signals.
    """
    bars = tuple(bars[-LOOKBACK:])
    if len(bars) < CONFIG.base_sessions + 3:
        return None
    if any(
        not b.period_complete or b.contains_provisional or b.contains_roll_event
        or not all(isfinite(x) and x > 0 for x in (b.open, b.high, b.low, b.close, b.volume))
        or not b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high
        for b in bars
    ) or any(a.period_end >= b.period_start for a, b in zip(bars, bars[1:])):
        return None
    blocked_until = -1
    latest = None
    for launch in range(CONFIG.base_sessions, len(bars)):
        momentum_start = _momentum_start(bars, launch)
        if momentum_start is not None and (
            momentum_start - CONFIG.base_sessions > blocked_until
            or (latest is not None and latest["stage"] == "invalidated"
                and "support-lost" in latest["reasons"]
                and momentum_start > blocked_until)
        ):
            base = bars[momentum_start - CONFIG.base_sessions:momentum_start]
            boundary = max(b.high for b in base)
            if bars[launch].close >= boundary * (1 + CONFIG.breakout_buffer):
                atr = _base_atr(base, boundary)
                latest, blocked_until = _follow(
                    bars, momentum_start, boundary, atr, momentum_confirmed=launch
                )
                continue
        if launch - CONFIG.base_sessions <= blocked_until:
            continue
        base = bars[launch - CONFIG.base_sessions:launch]
        boundary = max(b.high for b in base)
        floor = min(b.low for b in base)
        bar = bars[launch]
        if (boundary / floor - 1 > CONFIG.max_base_range
            or bar.close < boundary * (1 + CONFIG.breakout_buffer)
            or bar.close <= bar.open
            or bar.volume < fmean(b.volume for b in base) * CONFIG.breakout_volume_ratio):
            continue
        latest, blocked_until = _follow(bars, launch, boundary, _base_atr(base, boundary))
    return latest


def _base_atr(base, boundary):
    return max(boundary * .005, fmean(
        max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close))
        for a, b in zip(base[-15:], base[-14:])
    ))


def _momentum_start(bars, index):
    def strong(i):
        bar = bars[i]
        return (bar.close >= bars[i - 1].close * (1 + CONFIG.momentum_daily_gain)
                and bar.close >= bar.open and bar.close >= bar.high * .97)

    if index < CONFIG.base_sessions + 1 or not strong(index - 1) or not strong(index):
        return None
    if strong(index - 2):
        return None  # Only the first pair, never successive pairs inside one advance.
    start = index - 1
    while start > max(CONFIG.base_sessions, index - 3):
        previous = bars[start - 1]
        if previous.close < bars[start - 2].close * 1.03 or previous.close <= previous.open:
            break
        start -= 1
    return start


def _flag_window(bars, peak_index, end):
    peak_bar = bars[peak_index]
    # Include a rejection session as geometry, without inferring its high/low order.
    start = peak_index if peak_bar.close <= peak_bar.high * (1 - CONFIG.min_pullback_fraction) else peak_index + 1
    window = bars[start:end + 1]
    if not window:
        return None
    size = len(window)
    upper, lower = max(b.high for b in window), min(b.low for b in window)
    xs = [i - (size - 1) / 2 for i in range(size)]
    denominator = sum(x * x for x in xs)
    def slope(values):
        return sum(x * y for x, y in zip(xs, values)) / denominator if denominator else 0.
    high_slope = slope([b.high for b in window]) / peak_bar.high
    low_slope = slope([b.low for b in window]) / peak_bar.high
    split = max(1, size // 2)
    early_range = fmean(b.high - b.low for b in window[:split])
    late_range = fmean(b.high - b.low for b in window[split:] or window[-1:])
    expansion = late_range / early_range if early_range > 0 else (1. if late_range == 0 else None)
    reasons = []
    if size < CONFIG.min_flag_sessions:
        reasons.append("flag-window-too-short")
    if size > CONFIG.max_pullback_sessions:
        reasons.append("flag-window-too-long")
    if (upper - lower) / peak_bar.high > CONFIG.max_flag_range:
        reasons.append("flag-range-too-wide")
    if max(high_slope, low_slope) > CONFIG.max_flag_rail_rise_per_session:
        reasons.append("flag-rails-rising")
    if expansion is None or expansion > CONFIG.max_flag_range_expansion:
        reasons.append("flag-range-expanding")
    return {
        "start_date": window[0].period_end.isoformat(),
        "end_date": window[-1].period_end.isoformat(), "sessions": size,
        "phase": "early" if size < 3 else "consolidating",
        "qualified": not reasons, "reasons": reasons,
        "upper": upper, "lower": lower,
        "high_slope_percent_per_session": round(high_slope * 100, 4),
        "low_slope_percent_per_session": round(low_slope * 100, 4),
        "range_percent": round((upper - lower) / peak_bar.high * 100, 4),
        "range_expansion_ratio": round(expansion, 4) if expansion is not None else None,
    }


def _follow(bars, launch: int, boundary: float, atr: float, *, momentum_confirmed=None):
    momentum = momentum_confirmed is not None
    confirmed_launch = momentum_confirmed if momentum else launch
    origin = bars[launch - 1].close if momentum else boundary
    peak_index = max(range(launch, confirmed_launch + 1), key=lambda i: bars[i].high)
    peak = bars[peak_index].high
    pullback_start = None
    confirmation = None
    confirmed_stop = None
    trough_close = None
    recovery_high = None
    state = "launching"
    reason = None
    support = boundary
    terminal = len(bars) - 1
    for index in range(confirmed_launch + 1, len(bars)):
        bar = bars[index]
        if pullback_start is None:
            if not momentum and _momentum_start(bars, index) == launch:
                momentum, confirmed_launch, origin = True, index, bars[launch - 1].close
            # A daily outside bar cannot establish intraday peak-to-trough order.
            if bar.high > peak:
                peak, peak_index = bar.high, index
            drawdown = (peak - bar.close) / peak
            if drawdown < CONFIG.min_pullback_fraction:
                if index - launch >= CONFIG.max_launch_sessions:
                    state, reason, terminal = "expired", "launch-window-expired", index
                    break
                continue
            # A new-high session is not evidence of a subsequent daily correction.
            # Wait for a later bar instead of permanently rejecting the whole launch.
            if peak_index == index and bar.close >= boundary:
                if index - launch >= CONFIG.max_launch_sessions:
                    state, reason, terminal = "expired", "launch-window-expired", index
                    break
                continue
            pullback_start = index
            gain = peak / origin - 1
            retracement = CONFIG.momentum_max_retracement if momentum else CONFIG.max_retracement
            support = max(boundary, peak - (peak - origin) * retracement)
            minimum = CONFIG.momentum_min_gain if momentum else CONFIG.min_impulse_gain
            maximum = CONFIG.momentum_max_gain if momentum else CONFIG.max_impulse_gain
            if not minimum <= gain <= maximum:
                state, reason, terminal = "invalidated", "launch-strength-out-of-range", index
                break
            if peak_index == index:
                state, reason, terminal = "invalidated", "outside-bar-order-ambiguous", index
                break
        invalidation = support - atr * .25
        if bar.close < invalidation or (confirmed_stop is not None and bar.close < confirmed_stop):
            state, reason, terminal = "invalidated", "support-lost", index
            break
        if bar.high >= peak:
            state, reason, terminal = "completed", "prior-high-reached", index
            break
        # Wave counting is structural, independent of the volume-based entry gate.
        if recovery_high is not None:
            if bar.close <= recovery_high * (1 - CONFIG.min_pullback_fraction):
                state, reason, terminal = "completed", "second-pullback-started", index
                break
            recovery_high = max(recovery_high, bar.high)
        else:
            trough_close = bar.close if trough_close is None else min(trough_close, bar.close)
            if bar.close >= trough_close * (1 + CONFIG.min_pullback_fraction):
                recovery_high = bar.high
        if confirmation is not None:
            if index - confirmation > CONFIG.confirmation_fresh_sessions:
                state, reason, terminal = "expired", "confirmation-stale", index
                break
            state = "pullback-confirmed"
            continue
        if index - pullback_start >= CONFIG.max_pullback_sessions:
            state, reason, terminal = "expired", "pullback-window-expired", index
            break
        recent = bars[peak_index + 1:index + 1]
        rally_volume = fmean(b.volume for b in bars[launch:peak_index + 1])
        volume_ratio = fmean(b.volume for b in recent) / rally_volume
        orderly = volume_ratio <= CONFIG.max_pullback_volume_ratio
        if momentum:
            turnover_ratio = fmean(b.volume for b in recent) / max(b.volume for b in bars[launch:peak_index + 1])
            orderly = (turnover_ratio <= CONFIG.max_pullback_volume_ratio
                       or (turnover_ratio <= CONFIG.momentum_max_turnover_ratio
                           and bar.close >= bars[index - 1].close))
        state = "pullback-observation" if orderly else "disorderly"
        if index - pullback_start < 2:
            continue
        prior = bars[max(peak_index + 1, index - 3):index]
        prior_volume_ratio = fmean(b.volume for b in bars[peak_index + 1:index]) / rally_volume
        trigger = max(b.high for b in prior)
        prior_flag = _flag_window(bars, peak_index, index - 1)
        if (bar.close > trigger and bar.close > bar.open
            and (bar.close - bar.low) / max(bar.high - bar.low, .001) >= .65
            and bar.volume >= fmean(b.volume for b in prior)
            and prior_volume_ratio <= CONFIG.max_pullback_volume_ratio
            and prior_flag is not None and prior_flag["qualified"]):
            confirmation = index
            confirmed_stop = max(invalidation, min(b.low for b in bars[peak_index + 1:index + 1]) - atr * .25)
            recovery_high = max(recovery_high or bar.high, bar.high)
            state = "pullback-confirmed"
    if pullback_start is None:
        return None, terminal
    last = bars[-1]
    end = min(terminal, len(bars) - 1)
    pullback = bars[peak_index + 1:(confirmation if confirmation is not None else end + 1)]
    if not pullback:
        pullback = bars[pullback_start:pullback_start + 1]
    volume_ratio = fmean(b.volume for b in pullback) / fmean(b.volume for b in bars[launch:peak_index + 1])
    invalidation = confirmed_stop if confirmed_stop is not None else support - atr * .25
    risk = last.close - invalidation
    reward = peak - last.close
    rr = reward / risk if risk > 0 and reward > 0 else None
    reasons = [reason] if reason else []
    if state == "disorderly":
        reasons.append("pullback-volume-not-contracted")
    eligible = state in SCREENABLE_STATES
    flag = _flag_window(bars, peak_index, confirmation - 1 if confirmation is not None else end)
    window_start = max(0, len(bars) - CONFIG.observation_window_sessions)
    if launch < window_start:
        eligible = False
        reasons.append("launch-outside-observation-window")
    if flag is None or not flag["qualified"]:
        eligible = False
        reasons.extend(flag["reasons"] if flag is not None else ["flag-window-too-short"])
    if state == "pullback-confirmed" and (rr is None or rr < 1.5):
        eligible = False
        reasons.append("insufficient-space-to-prior-high")
    gain = peak / origin - 1
    turnover_reference = max(b.volume for b in bars[launch:peak_index + 1])
    turnover_ratio = fmean(b.volume for b in pullback) / turnover_reference
    score = min(100., 40 + min(gain, .25) * 100 + max(0., 1 - volume_ratio) * 25
                + (10 if state == "pullback-confirmed" else 0))
    return {
        "kind": KIND, "display_name": "强势股首次回踩", "algorithm_version": ALGORITHM_VERSION,
        "launch_type": "strong-momentum" if momentum else "base-breakout",
        "observation_window_sessions": CONFIG.observation_window_sessions,
        "observation_window_start_date": bars[window_start].period_end.isoformat(),
        "observation_window_end_date": last.period_end.isoformat(),
        "flag_window": flag,
        "launch_confirmation_date": bars[confirmed_launch].period_end.isoformat(),
        "impulse_origin_price": origin,
        "volume_regime": (("turnover-contraction" if turnover_ratio <= CONFIG.max_pullback_volume_ratio
                           else "elevated-turnover-digestion" if turnover_ratio <= CONFIG.momentum_max_turnover_ratio
                           else "expanding-turnover") if momentum
                          else "rally-mean-contraction" if volume_ratio <= CONFIG.max_pullback_volume_ratio
                          else "expanding-volume"),
        "turnover_reference_volume": turnover_reference,
        "pullback_turnover_ratio": round(turnover_ratio, 4),
        "stage": state, "screen_eligible": eligible, "score": round(score, 4),
        "start_date": flag["start_date"] if flag is not None else pullback[0].period_end.isoformat(),
        "recognition_date": bars[pullback_start].period_end.isoformat(),
        "end_date": bars[end].period_end.isoformat(),
        "launch_date": bars[launch].period_end.isoformat(),
        "peak_date": bars[peak_index].period_end.isoformat(),
        "confirmation_date": bars[confirmation].period_end.isoformat() if confirmation is not None else None,
        "as_of_date": last.period_end.isoformat(), "latest_close": last.close,
        "lower": support, "upper": support + atr, "center": support + atr / 2,
        "breakout_price": boundary, "peak_price": peak,
        "invalidation_price": invalidation, "first_target_price": peak,
        "first_risk_reward": round(rr, 4) if rr is not None else None,
        "impulse_gain_percent": round(gain * 100, 4),
        "pullback_depth_percent": round((peak - min(b.low for b in pullback)) / peak * 100, 4),
        "pullback_volume_ratio": round(volume_ratio, 4),
        "pullback_sessions": len(pullback),
        "pullback_metric_start_date": pullback[0].period_end.isoformat(),
        "pullback_metric_end_date": pullback[-1].period_end.isoformat(),
        "reasons": reasons, "parameters": asdict(CONFIG),
        "missing_evidence": ["sector-relative-strength", "market-permission", "intraday-confirmation"],
        "uncertainty": "Daily price-volume hypothesis; no institutional-position or execution confirmation. Prior high is a reference, not a forecast.",
    }, terminal
