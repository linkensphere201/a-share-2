"""Causal low-position platform retests, owned by shared pattern analysis."""

from dataclasses import asdict, dataclass
from math import isfinite
from statistics import fmean
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar


ALGORITHM_VERSION = "low-base-platform-pullback-v2"
LAUNCH_TYPE = "low-base-platform"


@dataclass(frozen=True, slots=True)
class LowBasePullbackConfig:
    context_sessions: int = 60
    observation_window_sessions: int = 30
    volume_baseline_sessions: int = 20
    max_origin_above_context_low: float = .20
    max_origin_range_position: float = .70
    min_pulse_daily_gain: float = .02
    min_pulse_volume_ratio: float = 1.5
    max_pulse_sessions: int = 3
    min_pulse_gain: float = .025
    max_pulse_gain: float = .22
    min_platform_sessions: int = 3
    max_platform_sessions: int = 22
    max_platform_range: float = .14
    max_platform_close_range: float = .09
    max_mean_body_fraction: float = .025
    max_platform_volume_ratio: float = .90
    max_platform_extension: float = .025
    min_retest_sessions: int = 2
    max_retest_sessions: int = 4
    min_retest_decline: float = .01
    max_retest_pulse_volume_ratio: float = .70
    max_retest_platform_volume_ratio: float = .90
    support_origin_allowance: float = .02
    support_platform_allowance: float = .05
    breakout_buffer: float = .003
    max_retest_confirmation_delay: int = 5
    confirmation_fresh_sessions: int = 2
    max_confirmation_extension: float = .06


CONFIG = LowBasePullbackConfig()


def detect_low_base_pullback(bars: Sequence[AnalysisBar]) -> dict[str, object] | None:
    """A bounded research candidate, not proof of accumulation or future return."""
    bars = tuple(bars[-250:])
    if len(bars) < CONFIG.context_sessions + CONFIG.min_platform_sessions + 3:
        return None
    if any(
        not b.period_complete or b.contains_provisional or b.contains_roll_event
        or not all(isfinite(x) and x > 0 for x in (b.open, b.high, b.low, b.close, b.volume))
        or not b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high
        for b in bars
    ) or any(a.period_end >= b.period_start for a, b in zip(bars, bars[1:])):
        return None
    candidates = []
    blocked_until = -1
    # Older seeds are followed too: moving the observation window must not rearm a leg.
    for launch in range(CONFIG.context_sessions, len(bars) - 1):
        if launch <= blocked_until:
            continue
        context = bars[launch - CONFIG.context_sessions:launch]
        baseline = context[-CONFIG.volume_baseline_sessions:]
        floor, ceiling = min(b.low for b in context), max(b.high for b in context)
        origin, first = context[-1].close, bars[launch]
        if (origin / floor - 1 > CONFIG.max_origin_above_context_low
            or (origin - floor) / max(ceiling - floor, floor * .01) > CONFIG.max_origin_range_position
            or first.close < origin * (1 + CONFIG.min_pulse_daily_gain)
            or first.close <= first.open
            or first.close < max(b.close for b in baseline) * .99
            or first.volume < fmean(b.volume for b in baseline) * CONFIG.min_pulse_volume_ratio):
            continue
        peak_index = launch
        for index in range(launch + 1, min(len(bars), launch + CONFIG.max_pulse_sessions)):
            if bars[index].high <= bars[peak_index].high or bars[index].close < bars[index - 1].close:
                break
            peak_index = index
        peak = bars[peak_index].high
        if not CONFIG.min_pulse_gain <= peak / origin - 1 <= CONFIG.max_pulse_gain:
            continue
        result, terminal = _follow(bars, launch, peak_index, origin, floor)
        blocked_until = terminal
        if result is not None:
            candidates.append(result)
    if not candidates:
        return None
    return max(candidates, key=lambda p: (p["screen_eligible"], p["launch_date"]))


def _geometry(platform):
    lower, upper = min(b.low for b in platform), max(b.high for b in platform)
    close_range = max(b.close for b in platform) / min(b.close for b in platform) - 1
    body = fmean(abs(b.close - b.open) / b.open for b in platform)
    return lower, upper, close_range, body


def _platform_evidence(platform):
    """Describe the pre-retest plateau, never a future outcome or entry quality."""
    lower, upper, close_range, body = _geometry(platform)
    bullish = [b.volume for b in platform if b.close > b.open]
    bearish = [b.volume for b in platform if b.close < b.open]
    ratio = fmean(bullish) / fmean(bearish) if bullish and bearish else None
    # Remove the largest bullish day to expose single-pulse volume illusions.
    trimmed = sorted(bullish)[:-1]
    robust = fmean(trimmed) / fmean(bearish) if trimmed and bearish else None
    if len(bullish) < 3 or len(bearish) < 2:
        quality = "insufficient-directional-evidence"
    elif robust > 1:
        quality = "persistent-bullish-volume"
    elif ratio > 1:
        quality = "single-pulse-dominated"
    else:
        quality = "no-persistent-bullish-advantage"
    return {
        "start_date": platform[0].period_end.isoformat(),
        "end_date": platform[-1].period_end.isoformat(), "sessions": len(platform),
        "range_percent": round((upper / lower - 1) * 100, 4),
        "close_range_percent": round(close_range * 100, 4),
        "close_drift_percent": round((platform[-1].close / platform[0].close - 1) * 100, 4),
        "mean_body_percent": round(body * 100, 4),
        "small_body_fraction": round(sum(abs(b.close / b.open - 1) <= CONFIG.max_mean_body_fraction
                                         for b in platform) / len(platform), 4),
        "bullish_sessions": len(bullish), "bearish_sessions": len(bearish),
        "bullish_bearish_volume_ratio": round(ratio, 4) if ratio is not None else None,
        "trimmed_bullish_bearish_volume_ratio": round(robust, 4) if robust is not None else None,
        "volume_quality": quality,
    }


def _retest(bars, start, end, pulse_volume):
    for size in range(CONFIG.min_retest_sessions, CONFIG.max_retest_sessions + 1):
        split = end + 1 - size
        platform, retest = bars[start:split], bars[split:end + 1]
        if len(platform) < CONFIG.min_platform_sessions:
            continue
        platform_volume = fmean(b.volume for b in platform)
        retest_volume = fmean(b.volume for b in retest)
        if (retest[-1].close > bars[split - 1].close * (1 - CONFIG.min_retest_decline)
            or retest[-1].close > bars[end - 1].close * 1.005
            or sum(bars[i].close < bars[i - 1].close for i in range(split, end + 1)) < 2
            or retest_volume > pulse_volume * CONFIG.max_retest_pulse_volume_ratio
            or retest_volume > platform_volume * CONFIG.max_retest_platform_volume_ratio):
            continue
        return split, retest_volume / pulse_volume, retest_volume / platform_volume
    return None


def _follow(bars, launch, peak_index, origin, context_low):
    start = peak_index + 1
    pulse = bars[launch:start]
    pulse_volume = fmean(b.volume for b in pulse)
    peak = bars[peak_index].high
    support = origin * (1 - CONFIG.support_origin_allowance)
    recognized = None
    confirmation = None
    evidence = None
    boundary = None
    state, reason = "launching", None
    terminal = len(bars) - 1
    for end in range(start, len(bars)):
        current = bars[end]
        if end == start + 1:
            support = max(support, min(b.low for b in bars[start:end + 1])
                          * (1 - CONFIG.support_platform_allowance))
        if current.close < support:
            state, reason, terminal = "invalidated", "support-lost", end
            break
        if confirmation is not None:
            if current.close < boundary * (1 - CONFIG.support_origin_allowance):
                state, reason, terminal = "invalidated", "breakout-hold-lost", end
                break
            if (end - confirmation > CONFIG.confirmation_fresh_sessions
                or current.close > boundary * (1 + CONFIG.max_confirmation_extension)):
                state, reason, terminal = "expired", "confirmation-stale-or-extended", end
                break
            continue
        prior = bars[start:end]
        if (recognized is not None and prior
            and end - evidence["retest_end"] <= CONFIG.max_retest_confirmation_delay):
            boundary = max(b.high for b in prior)
            if (current.close > boundary * (1 + CONFIG.breakout_buffer)
                and current.close > current.open
                and current.volume >= fmean(b.volume for b in prior[-3:])):
                confirmation = end
                state = "pullback-confirmed"
                # Retain the latest qualified retest; confirmation is not its volume evidence.
                evidence = {**evidence, "platform_end": end - 1}
                if current.close > boundary * (1 + CONFIG.max_confirmation_extension):
                    state, reason, terminal = "expired", "confirmation-stale-or-extended", end
                    break
                continue
        platform = bars[start:end + 1]
        if len(platform) > CONFIG.max_platform_sessions:
            state, reason, terminal = "expired", "platform-window-expired", end
            break
        if current.high > peak * (1 + CONFIG.max_platform_extension):
            state, reason, terminal = "completed", "advance-without-qualified-retest", end
            break
        if len(platform) < CONFIG.min_platform_sessions + CONFIG.min_retest_sessions:
            continue
        lower, upper, close_range, body = _geometry(platform)
        orderly = (upper / lower - 1 <= CONFIG.max_platform_range
                   and close_range <= CONFIG.max_platform_close_range
                   and body <= CONFIG.max_mean_body_fraction
                   and fmean(b.volume for b in platform) <= pulse_volume * CONFIG.max_platform_volume_ratio)
        retest = _retest(bars, start, end, pulse_volume) if orderly else None
        state = "pullback-observation" if retest else "disorderly"
        if retest:
            recognized = end if recognized is None else recognized
            evidence = {"retest_start": retest[0], "retest_end": end,
                        "pulse_ratio": retest[1], "platform_ratio": retest[2], "platform_end": end}
        elif not orderly:
            # Losing platform quality cancels earlier confirmation permission.
            recognized, evidence = None, None
    if evidence is None:
        return None, terminal
    platform = bars[start:evidence["platform_end"] + 1]
    retest = bars[evidence["retest_start"]:evidence["retest_end"] + 1]
    lower, upper, close_range, body = _geometry(platform)
    last = bars[-1]
    window_start = max(0, len(bars) - CONFIG.observation_window_sessions)
    reasons = [reason] if reason else []
    if launch < window_start:
        reasons.append("launch-outside-observation-window")
    if state == "disorderly":
        reasons.append("retest-not-currently-qualified")
    eligible = not reasons and state in ("pullback-observation", "pullback-confirmed")
    shape = _platform_evidence(bars[start:evidence["retest_start"]])
    score = (45 + 20 * max(0., 1 - evidence["pulse_ratio"])
             + 15 * max(0., 1 - shape["close_range_percent"] / (100 * CONFIG.max_platform_close_range)))
    stamp = lambda i: bars[i].period_end.isoformat()
    return {
        "kind": "first-pullback-range", "display_name": "Low-base platform pullback",
        "algorithm_version": ALGORITHM_VERSION, "launch_type": LAUNCH_TYPE,
        "stage": state, "screen_eligible": eligible, "score": round(score, 4),
        "launch_date": stamp(launch), "launch_confirmation_date": stamp(peak_index),
        "peak_date": stamp(peak_index), "recognition_date": stamp(recognized),
        "confirmation_date": stamp(confirmation) if confirmation is not None else None,
        "start_date": stamp(start), "end_date": stamp(terminal), "as_of_date": stamp(len(bars) - 1),
        "latest_close": last.close, "lower": lower, "upper": upper, "center": (lower + upper) / 2,
        "invalidation_price": support, "breakout_price": boundary if confirmation is not None else upper,
        "first_target_price": None, "first_risk_reward": None,
        "platform_shape": shape,
        "impulse_origin_price": origin, "peak_price": peak,
        "impulse_gain_percent": round((peak / origin - 1) * 100, 4),
        "origin_above_context_low_percent": round((origin / context_low - 1) * 100, 4),
        "platform_range_percent": round((upper / lower - 1) * 100, 4),
        "platform_close_range_percent": round(close_range * 100, 4),
        "platform_mean_body_percent": round(body * 100, 4),
        "platform_volume_ratio": round(fmean(b.volume for b in platform) / pulse_volume, 4),
        "pullback_volume_ratio": round(evidence["pulse_ratio"], 4),
        "pullback_platform_volume_ratio": round(evidence["platform_ratio"], 4),
        "pullback_depth_percent": round((upper - min(b.low for b in retest)) / upper * 100, 4),
        "pullback_sessions": len(retest), "pullback_metric_start_date": retest[0].period_end.isoformat(),
        "pullback_metric_end_date": retest[-1].period_end.isoformat(),
        "volume_regime": "pulse-and-platform-contraction",
        "observation_window_sessions": CONFIG.observation_window_sessions,
        "observation_window_start_date": stamp(window_start), "observation_window_end_date": stamp(len(bars) - 1),
        "flag_window": {"start_date": stamp(start), "end_date": stamp(evidence["platform_end"]),
                        "sessions": len(platform), "phase": "low-base-platform", "qualified": eligible},
        "parameters": asdict(CONFIG), "reasons": reasons,
        "missing_evidence": ["sector-relative-strength", "market-permission", "intraday-confirmation"],
        "uncertainty": "Research structure only; no institutional holdings or future doubling inference. No projected target after breakout.",
    }, terminal
