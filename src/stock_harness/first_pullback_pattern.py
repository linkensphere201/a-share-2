"""Causal daily first-pullback lifecycle owned by pattern analysis, not screening."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from statistics import fmean
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar


ALGORITHM_VERSION = "strong-first-pullback-v1"
KIND = "first-pullback-range"
SCREENABLE_STATES = ("pullback-observation", "pullback-confirmed")
LOOKBACK = 250


@dataclass(frozen=True, slots=True)
class FirstPullbackConfig:
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


CONFIG = FirstPullbackConfig()


def detect_first_pullback(bars: Sequence[AnalysisBar]) -> dict[str, object] | None:
    """Use only the supplied final daily prefix; never rearm inside an old leg.

    A new launch requires a complete base after the preceding lifecycle ended.
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
        atr = fmean(max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close))
                    for a, b in zip(base[-15:], base[-14:]))
        latest, blocked_until = _follow(bars, launch, boundary, max(atr, boundary * .005))
    return latest


def _follow(bars, launch: int, boundary: float, atr: float):
    peak_index = launch
    peak = bars[launch].high
    pullback_start = None
    confirmation = None
    confirmed_stop = None
    state = "launching"
    reason = None
    support = boundary
    terminal = len(bars) - 1
    for index in range(launch + 1, len(bars)):
        bar = bars[index]
        if pullback_start is None:
            # A daily outside bar cannot establish intraday peak-to-trough order.
            if bar.high > peak:
                peak, peak_index = bar.high, index
            drawdown = (peak - bar.close) / peak
            if drawdown < CONFIG.min_pullback_fraction:
                if index - launch >= CONFIG.max_launch_sessions:
                    state, reason, terminal = "expired", "launch-window-expired", index
                    break
                continue
            pullback_start = index
            gain = peak / boundary - 1
            support = max(boundary, peak - (peak - boundary) * CONFIG.max_retracement)
            if not CONFIG.min_impulse_gain <= gain <= CONFIG.max_impulse_gain:
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
        if confirmation is not None:
            recovery_high = max(b.high for b in bars[confirmation:index])
            if bar.close <= recovery_high * (1 - CONFIG.min_pullback_fraction):
                state, reason, terminal = "completed", "second-pullback-started", index
                break
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
        state = "pullback-observation" if volume_ratio <= CONFIG.max_pullback_volume_ratio else "disorderly"
        if index - pullback_start < 2:
            continue
        prior = bars[max(peak_index + 1, index - 3):index]
        prior_volume_ratio = fmean(b.volume for b in bars[peak_index + 1:index]) / rally_volume
        trigger = max(b.high for b in prior)
        if (bar.close > trigger and bar.close > bar.open
            and (bar.close - bar.low) / max(bar.high - bar.low, .001) >= .65
            and bar.volume >= fmean(b.volume for b in prior)
            and prior_volume_ratio <= CONFIG.max_pullback_volume_ratio):
            confirmation = index
            confirmed_stop = max(invalidation, min(b.low for b in bars[peak_index + 1:index + 1]) - atr * .25)
            state = "pullback-confirmed"
            if bar.high >= peak:
                state, reason, terminal = "completed", "prior-high-reached", index
                break
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
    if state == "pullback-confirmed" and (rr is None or rr < 1.5):
        eligible = False
        reasons.append("insufficient-space-to-prior-high")
    gain = peak / boundary - 1
    score = min(100., 40 + min(gain, .25) * 100 + max(0., 1 - volume_ratio) * 25
                + (10 if state == "pullback-confirmed" else 0))
    return {
        "kind": KIND, "display_name": "强势股首次回踩", "algorithm_version": ALGORITHM_VERSION,
        "stage": state, "screen_eligible": eligible, "score": round(score, 4),
        "start_date": bars[pullback_start].period_end.isoformat(),
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
        "pullback_sessions": end - pullback_start + 1,
        "reasons": reasons, "parameters": asdict(CONFIG),
        "missing_evidence": ["sector-relative-strength", "market-permission", "intraday-confirmation"],
        "uncertainty": "Daily price-volume hypothesis; no institutional-position or execution confirmation. Prior high is a reference, not a forecast.",
    }, terminal
