"""Causal detection of a demand-led accumulation range after a sustained decline."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from statistics import fmean
from typing import Mapping, Sequence

from stock_harness.analysis_inputs import AnalysisBar


ALGORITHM_VERSION = "decline-platform-accumulation-v3"
ANALYSIS_LOOKBACK = 170


@dataclass(frozen=True, slots=True)
class AccumulationPatternConfig:
    decline_windows: tuple[int, ...] = (60, 80, 100)
    lift_window: int = 10
    min_platform_window: int = 8
    max_platform_window: int = 20
    followup_window: int = 5
    min_decline_percent: float = 10.0
    min_deceleration: float = 0.25
    min_quality_score: float = 60.0
    invalidation_atr: float = 0.5
    breakout_atr: float = 0.5

    def __post_init__(self) -> None:
        if (not self.decline_windows or min(self.decline_windows) < 60
                or max(self.decline_windows) > 100 or self.lift_window != 10
                or not 8 <= self.min_platform_window <= self.max_platform_window <= 20
                or not 0 <= self.followup_window <= 5):
            raise ValueError("unsupported accumulation search windows")
        if not all(isfinite(value) for value in (
            self.min_decline_percent, self.min_deceleration, self.min_quality_score,
            self.invalidation_atr, self.breakout_atr,
        )) or not (0 < self.min_decline_percent < 100
                   and 0 <= self.min_deceleration <= 1
                   and 0 <= self.min_quality_score <= 100
                   and self.invalidation_atr > 0 and self.breakout_atr > 0):
            raise ValueError("invalid accumulation thresholds")


@dataclass(frozen=True, slots=True)
class AccumulationPattern:
    score: float
    start_date: str
    end_date: str
    lower: float
    upper: float
    evidence: dict[str, object]
    stage: str = "accumulation"


def detect_accumulation_pattern(
    bars: Sequence[AnalysisBar],
    *,
    limit_up_dates: frozenset[str] | None = None,
    turnover_by_date: Mapping[str, float] | None = None,
    config: AccumulationPatternConfig = AccumulationPatternConfig(),
) -> AccumulationPattern | None:
    """Detect a bounded daily setup and replay only its observed follow-up bars."""
    history = list(bars[-ANALYSIS_LOOKBACK:])
    if len(history) < min(config.decline_windows) + config.lift_window + config.min_platform_window:
        return None
    if any(
        not all(isfinite(v) and v > 0 for v in (b.open, b.high, b.low, b.close))
        or not isfinite(b.volume) or b.volume < 0
        or b.low > min(b.open, b.close) or b.high < max(b.open, b.close)
        or b.contains_provisional or not b.period_complete
        or b.period_start != b.period_end or b.contains_roll_event
        for b in history
    ) or any(a.period_end >= b.period_end for a, b in zip(history, history[1:])):
        return None

    # Search recent endpoints so a breakout cannot erase its preceding base.
    candidates: list[tuple[int, AccumulationPattern]] = []
    for lag in range(config.followup_window + 1):
        end = len(history) - lag
        for width in range(config.min_platform_window, config.max_platform_window + 1):
            for decline_width in config.decline_windows:
                start = end - width - config.lift_window - decline_width
                if start < 0:
                    continue
                candidate = _candidate(
                    history[start:end], decline_width, config,
                    limit_up_dates, turnover_by_date,
                )
                if candidate is not None:
                    candidates.append((end, candidate))
            # Confirm the first low before searching its rebound and higher retest.
            platform_start = end - width
            for trough in range(max(59, platform_start - 46), platform_start - 15):
                if history[trough].low != min(b.low for b in history[trough - 3:trough + 4]):
                    continue
                recovery_sessions = platform_start - trough - 1
                for decline_width in config.decline_windows:
                    start = trough + 1 - decline_width
                    if start < 0:
                        continue
                    candidate = _candidate(
                        history[start:end], decline_width, config,
                        limit_up_dates, turnover_by_date,
                        recovery_sessions=recovery_sessions,
                    )
                    if candidate is not None:
                        candidates.append((end, candidate))
    if not candidates:
        return None
    formed = [(end, p) for end, p in candidates if p.stage == "accumulation"]
    # Prefer the most recent established base, then a well-supported longer range.
    end, selected = max(
        formed or candidates,
        key=lambda pair: (pair[0], pair[1].score + float(pair[1].evidence["platform_sessions"]) * .15),
    )
    return _follow_up(selected, history[end:], history[-1], limit_up_dates, config)


def _quantile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _unit(value: float) -> float:
    return max(0.0, min(1.0, value))


def _slope(values: Sequence[float]) -> float:
    middle = (len(values) - 1) / 2
    return sum((i - middle) * v for i, v in enumerate(values)) / max(
        sum((i - middle) ** 2 for i in range(len(values))), 1,
    )


def _ma(values: Sequence[AnalysisBar], width: int, offset: int = 0) -> float:
    end = len(values) - offset
    return fmean(b.close for b in values[end - width:end])


def _ratio(numerator: float, denominator: float) -> float:
    # Bounded evidence keeps all-up / all-down samples finite and serializable.
    return min(10.0, numerator / denominator) if denominator > 0 else (10.0 if numerator > 0 else 0.0)


def _demand(platform: Sequence[AnalysisBar], baseline: Sequence[AnalysisBar]) -> dict[str, object]:
    up = [b.volume for b in platform if b.close > b.open]
    down = [b.volume for b in platform if b.close < b.open]
    total = sum(b.volume for b in platform)
    prior = fmean(b.volume for b in baseline)
    ratio = _ratio(sum(up), sum(down))
    average_ratio = _ratio(fmean(up) if up else 0, fmean(down) if down else 0)
    dominant = max(range(len(platform)), key=lambda i: platform[i].volume)
    reduced = [b for i, b in enumerate(platform) if i != dominant]
    robust_ratio = _ratio(
        sum(b.volume for b in reduced if b.close > b.open),
        sum(b.volume for b in reduced if b.close < b.open),
    )
    expansion = fmean(b.volume for b in platform) / max(prior, 1)
    close_location = sum(
        b.volume * (b.close - b.low) / max(b.high - b.low, b.close * 1e-8)
        for b in platform
    ) / max(total, 1)
    down_days = [b.volume for a, b in zip(platform, platform[1:]) if b.close < a.close]
    repair_days = [b.volume for a, b in zip(platform, platform[1:]) if b.close > a.close]
    repair_ratio = _ratio(
        fmean(repair_days) if repair_days else 0,
        fmean(down_days) if down_days else 0,
    )
    persistence = sum(b.close > b.open and b.volume >= prior for b in platform) / len(platform)
    median_volume = _quantile([b.volume for b in platform], .5)
    local_persistence = sum(
        b.close > b.open and b.volume >= median_volume for b in platform
    ) / len(platform)
    share = max(b.volume for b in platform) / max(total, 1)
    moderate = _unit((expansion - .8) / .5) * _unit((3.5 - expansion) / 1.5)
    quality = (
        .20 * _unit((average_ratio - .8) / .8)
        + .15 * _unit((robust_ratio - .8) / .8)
        + .15 * _unit((close_location - .35) / .35)
        + .15 * _unit((repair_ratio - .8) / .8)
        + .15 * _unit(persistence / .4)
        + .20 * moderate
    ) * _unit((.5 - share) / .2)
    return {
        "up_volume": sum(up), "down_volume": sum(down),
        "up_down_volume_ratio": ratio, "average_up_down_volume_ratio": average_ratio,
        "robust_up_down_volume_ratio": robust_ratio, "platform_volume_ratio": expansion,
        "dominant_session_share": share, "volume_weighted_close_location": close_location,
        "repair_pullback_volume_ratio": repair_ratio, "demand_persistence": persistence,
        "platform_demand_persistence": local_persistence,
        "demand_quality": quality,
    }


def _candidate(
    bars: Sequence[AnalysisBar],
    decline_width: int,
    config: AccumulationPatternConfig,
    limit_up_dates: frozenset[str] | None,
    turnover_by_date: Mapping[str, float] | None,
    *, recovery_sessions: int | None = None,
) -> AccumulationPattern | None:
    decline = bars[:decline_width]
    recovery_width = recovery_sessions if recovery_sessions is not None else config.lift_window
    lift = bars[decline_width:decline_width + recovery_width]
    platform = bars[decline_width + recovery_width:]
    decline_return = (decline[-1].close / decline[0].close - 1) * 100
    ma20, ma40, ma60 = (_ma(decline, w) for w in (20, 40, 60))
    divergence = (ma60 / ma20 - 1) * 100
    earlier_slope = (_ma(decline, 20) - _ma(decline, 20, 20)) / 20
    decline_steps = [decline[i + 10].close - decline[i].close for i in range(0, len(decline) - 10, 10)]
    sustained = sum(step < 0 for step in decline_steps) / len(decline_steps)
    if (decline_return > -config.min_decline_percent or not ma20 < ma40 < ma60
            or earlier_slope >= 0 or sustained < .6):
        return None
    recent_slope = (_ma(bars, 20) - _ma(bars, 20, 10)) / 10
    deceleration = 1 - max(0, -recent_slope) / abs(earlier_slope)
    short_slope = (_ma(bars, 5) - _ma(bars, 5, 5)) / 5
    recent_divergence = (_ma(bars, 60) / _ma(bars, 20) - 1) * 100
    tr = [
        max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close))
        for a, b in zip(bars, bars[1:])
    ]
    atr = fmean(tr[-(len(platform) + 10):])
    if atr <= 0:
        return None

    early_bottom = _quantile([b.low for b in lift[:5]], .3)
    late_bottom = _quantile([b.low for b in lift[5:]], .3)
    recovery_evidence: dict[str, object] = {}
    if recovery_sessions is not None:
        recovery = _secondary_recovery(decline, lift, atr)
        if recovery is None:
            return None
        early_bottom, late_bottom, recovery_evidence = recovery
    bottom_lift = (late_bottom / early_bottom - 1) * 100
    lift_atr = (late_bottom - early_bottom) / atr
    lower = _quantile([b.low for b in platform], .2)
    upper = _quantile([b.high for b in platform], .8)
    extreme_low, extreme_high = min(b.low for b in platform), max(b.high for b in platform)
    half = len(platform) // 2
    centers = [(b.open + b.close) / 2 for b in platform]
    center_move_atr = _slope(centers) * (len(platform) - 1) / atr
    contraction = fmean(tr[-len(platform) + half:]) / max(
        fmean(tr[-len(platform):-len(platform) + half]), 1e-8,
    )
    small = sum(abs(b.close - b.open) <= max(b.close * .015, atr * .6) for b in platform)
    support_held = (
        _quantile([b.low for b in platform[half:]], .3)
        >= _quantile([b.low for b in platform[:half]], .3) - .3 * atr
        and min(b.close for b in platform) >= early_bottom
        and platform[-1].close >= lower - config.invalidation_atr * atr
    )
    if (not support_held or bottom_lift <= 0 or lift_atr < .3
            or center_move_atr < -.5 or center_move_atr > 2
            or (upper - lower) / atr > 4 or small / len(platform) < .65
            or contraction > 1.5):
        return None

    # Compare a secondary platform with its pullback, not the earlier rebound surge.
    baseline = lift[-10:] if recovery_sessions is not None else bars[decline_width - 10:decline_width + 10]
    demand = _demand(platform, baseline)
    decline_quality = (
        .35 * _unit(abs(decline_return) / 25) + .2 * sustained
        + .45 * _unit(deceleration)
    )
    percent_preference = _unit(1 - max(5 - bottom_lift, bottom_lift - 10, 0) / 10)
    lift_quality = .45 * percent_preference + .55 * _unit(lift_atr / 2)
    platform_quality = (
        .35 * _unit(1 - abs(center_move_atr) / 3)
        + .35 * small / len(platform) + .3 * _unit(1.5 - contraction)
    )
    scores = {
        "decline": round(20 * decline_quality, 2),
        "lift": round(20 * lift_quality, 2),
        "platform": round(25 * platform_quality, 2),
        "demand": round(35 * float(demand["demand_quality"]), 2),
    }
    score = round(sum(scores.values()), 2)
    slowing = (
        deceleration >= config.min_deceleration
        and short_slope >= -.03 * atr
        and recent_divergence < divergence
    )
    dry_retest = (
        recovery_sessions is not None
        and .5 <= float(demand["platform_volume_ratio"]) < 1
        and float(demand["platform_demand_persistence"]) >= .3
        and float(demand["average_up_down_volume_ratio"]) >= 1.2
        and float(demand["robust_up_down_volume_ratio"]) >= 1.1
        and float(demand["repair_pullback_volume_ratio"]) >= 1.1
        and float(demand["volume_weighted_close_location"]) >= .55
    )
    volume_confirmed = dry_retest or (
        float(demand["demand_persistence"]) >= .25
        and 1 <= float(demand["platform_volume_ratio"]) <= 3
    )
    established = (
        slowing and score >= config.min_quality_score
        and float(demand["up_down_volume_ratio"]) > 1
        and float(demand["average_up_down_volume_ratio"]) > 1
        and float(demand["robust_up_down_volume_ratio"]) >= 1
        and float(demand["dominant_session_share"]) <= .35
        and volume_confirmed
        and platform[-1].close <= upper + config.breakout_atr * atr
    )
    missing = []
    turnover_ratio = None
    if turnover_by_date is not None:
        values = [turnover_by_date.get(b.period_end.isoformat()) for b in bars[-(len(platform) + 20):]]
        if all(v is not None and isfinite(v) and v > 0 for v in values):
            turnover_ratio = fmean(values[-len(platform):]) / fmean(values[:-len(platform)])
    if turnover_ratio is None:
        missing.append("turnover-unavailable")
    if limit_up_dates is None:
        missing.append("limit-up-dates-unavailable")
    evidence: dict[str, object] = {
        "algorithm_version": ALGORITHM_VERSION,
        "pattern_type": "secondary-base" if recovery_sessions is not None else "decline-lift-platform",
        "recovery_sessions": recovery_width,
        "volume_baseline": "pre-platform-retest" if recovery_sessions is not None else "decline-lift",
        "demand_regime": "dry-up-retest" if dry_retest else "moderate-expansion",
        **recovery_evidence,
        "as_of_date": platform[-1].period_end.isoformat(),
        "decline_start_date": decline[0].period_start.isoformat(),
        "decline_end_date": decline[-1].period_end.isoformat(),
        "decline_return_percent": decline_return,
        "ma20_decline_percent": (ma20 / fmean(b.close for b in decline[:20]) - 1) * 100,
        "ma_divergence_percent": divergence, "downward_ma_stack": True,
        "recent_ma_divergence_percent": recent_divergence,
        "prior_ma20_slope": earlier_slope, "recent_ma20_slope": recent_slope,
        "ma5_slope": short_slope, "deceleration": deceleration, "decline_slowing": slowing,
        "sustained_decline_fraction": sustained,
        "bottom_lift_percent": bottom_lift, "bottom_lift_atr": lift_atr,
        "support_held": support_held, "atr": atr,
        "platform_sessions": len(platform),
        "platform_range_percent": (upper / lower - 1) * 100,
        "platform_return_percent": (platform[-1].close / platform[0].close - 1) * 100,
        "platform_center_move_atr": center_move_atr, "volatility_contraction": contraction,
        "small_body_sessions": small, "extreme_low": extreme_low, "extreme_high": extreme_high,
        "invalidation_price": lower - config.invalidation_atr * atr,
        "breakout_price": upper + config.breakout_atr * atr,
        "first_target_price": upper + (upper - lower),
        "score_components": scores, "missing_evidence": missing,
        "evidence_completeness": (4 - len(missing)) / 4,
        "turnover_ratio": turnover_ratio,
        "limit_up_count": None if limit_up_dates is None else sum(
            b.period_end.isoformat() in limit_up_dates for b in [*lift, *platform]
        ),
        "limit_up_policy": "accepted-not-required",
        "platform_average_volume": fmean(b.volume for b in platform),
        "platform_limit_up_dates": [] if limit_up_dates is None else [
            b.period_end.isoformat() for b in platform if b.period_end.isoformat() in limit_up_dates
        ],
        "config": asdict(config),
        **demand,
    }
    # A weakly established base remains inspectable but cannot enter the screener.
    reasons = []
    if not slowing:
        reasons.append("decline-not-decelerating")
    if not established:
        reasons.append("accumulation-quality-insufficient")
    evidence["reasons"] = reasons
    evidence["limit_up_events"] = _limit_events(
        [*lift, *platform], limit_up_dates, lower, atr,
        platform_start=recovery_width, buffer_atr=config.invalidation_atr,
    )
    return AccumulationPattern(
        score, platform[0].period_start.isoformat(), platform[-1].period_end.isoformat(),
        lower, upper, evidence, "accumulation" if established else "stabilizing",
    )


def _secondary_recovery(
    decline: Sequence[AnalysisBar], recovery: Sequence[AnalysisBar], atr: float,
) -> tuple[float, float, dict[str, object]] | None:
    first_low = decline[-1].low
    peak_index = max(range(len(recovery)), key=lambda i: recovery[i].close)
    rebound, pullback = recovery[:peak_index + 1], recovery[peak_index + 1:]
    if len(rebound) < 3 or not 3 <= len(pullback) <= 25:
        return None
    peak = recovery[peak_index].close
    second_low = min(b.low for b in pullback)
    amplitude = peak - first_low
    if amplitude <= 0:
        return None
    retracement = (peak - second_low) / amplitude
    rebound_volume = fmean(b.volume for b in rebound)
    volume_ratio = fmean(b.volume for b in pullback) / max(rebound_volume, 1)
    # A higher low alone is insufficient: reject deep, violent or supply-led retests.
    if (amplitude < max(first_low * .05, atr * 1.5)
            or min(b.low for b in recovery) < first_low - .3 * atr
            or second_low < first_low + .3 * atr
            or not .1 <= retracement <= .7
            or (peak - second_low) / peak > .15
            or volume_ratio > .9
            or any(b.open - b.close > 1.5 * atr for b in pullback)
            or any(b.close < a.close and b.volume > rebound_volume * 1.2
                   for a, b in zip([rebound[-1], *pullback], pullback))):
        return None
    return first_low, second_low, {
        "first_bottom_date": decline[-1].period_end.isoformat(),
        "first_bottom_price": first_low,
        "rebound_peak_date": recovery[peak_index].period_end.isoformat(),
        "rebound_peak_price": peak,
        "second_bottom_date": min(pullback, key=lambda b: b.low).period_end.isoformat(),
        "second_bottom_price": second_low,
        "rebound_percent": amplitude / first_low * 100,
        "pullback_sessions": len(pullback),
        "pullback_retracement": retracement,
        "pullback_volume_ratio": volume_ratio,
    }


def _limit_events(
    bars: Sequence[AnalysisBar], dates: frozenset[str] | None, lower: float, atr: float,
    *, platform_start: int = 0, buffer_atr: float = .5, followup: bool = False,
) -> list[dict[str, object]]:
    if dates is None:
        return []
    events = []
    for index, bar in enumerate(bars):
        day = bar.period_end.isoformat()
        if day not in dates:
            continue
        follow = bars[index + 1:index + 6]
        position = "lift" if index < platform_start else (
            "followup" if followup else "platform-start" if index - platform_start < 3
            else "platform-end" if index >= len(bars) - 3 else "platform-middle"
        )
        # A raised future platform must not retroactively invalidate an earlier lift event.
        support = (bar.low if position == "lift" else max(lower, bar.low)) - buffer_atr * atr
        held = all(b.close >= support for b in follow)
        normalized = bool(follow) and fmean(b.volume for b in follow[-3:]) <= bar.volume * 1.1
        state = "failed" if not held else (
            "digested" if len(follow) >= 2 and normalized else "pending"
        )
        events.append({
            "date": day, "observed_sessions": len(follow), "state": state,
            "support_price": support, "position": position,
            "event_volume": bar.volume, "followup_volumes": [b.volume for b in follow],
        })
    return events


def _follow_up(
    pattern: AccumulationPattern,
    follow: Sequence[AnalysisBar],
    latest: AnalysisBar,
    limit_dates: frozenset[str] | None,
    config: AccumulationPatternConfig,
) -> AccumulationPattern:
    evidence = dict(pattern.evidence)
    stage = pattern.stage
    transition_date = None
    atr = float(evidence["atr"])
    breakout_seen = False
    for index, bar in enumerate(follow):
        if bar.close < float(evidence["invalidation_price"]):
            stage, transition_date = "invalidated", bar.period_end.isoformat()
            break
        if stage == "stabilizing":
            continue
        above = bar.close > float(evidence["breakout_price"])
        persistent = index > 0 and follow[index - 1].close > float(evidence["breakout_price"])
        if above and (bar.volume >= float(evidence["platform_average_volume"]) * 1.2 or persistent):
            stage, transition_date = "breakout", bar.period_end.isoformat()
            breakout_seen = True
        elif breakout_seen and bar.close < pattern.upper:
            stage, transition_date = "invalidated", bar.period_end.isoformat()
            evidence["reasons"] = [*evidence["reasons"], "failed-breakout"]
            break

    events = [dict(event) for event in evidence["limit_up_events"]]
    # Extend existing event evidence using only newly observed sessions.
    for event in events:
        available = max(0, 5 - int(event["observed_sessions"]))
        extra = follow[:available]
        if extra:
            event["observed_sessions"] = int(event["observed_sessions"]) + len(extra)
            volumes = [*event["followup_volumes"], *(b.volume for b in extra)]
            event["followup_volumes"] = volumes
            if any(b.close < float(event["support_price"]) for b in extra):
                event["state"] = "failed"
            elif event["state"] == "pending" and int(event["observed_sessions"]) >= 2:
                if fmean(volumes[-3:]) <= float(event["event_volume"]) * 1.1:
                    event["state"] = "digested"
    events.extend(_limit_events(
        follow, limit_dates, pattern.lower, atr,
        buffer_atr=config.invalidation_atr, followup=True,
    ))
    if stage == "accumulation" and any(e["state"] == "failed" for e in events):
        stage = "stabilizing"
        evidence["reasons"] = [*evidence["reasons"], "limit-up-digestion-failed"]
    elif stage == "accumulation" and any(e["state"] == "pending" for e in events):
        stage = "pending-digestion"
    if follow and stage in {"accumulation", "pending-digestion"}:
        gentle = all(
            b.close >= pattern.lower and b.low >= float(evidence["invalidation_price"])
            and b.close <= float(evidence["breakout_price"])
            and abs(b.close - b.open) <= atr
            and 0 < b.volume <= float(evidence["platform_average_volume"]) * .9
            for b in follow
        ) and pattern.upper - min(b.close for b in follow) <= 2 * atr
        evidence["gentle_retest"] = gentle
        if not gentle:
            stage = "stabilizing"
            evidence["reasons"] = [*evidence["reasons"], "recent-platform-unconfirmed"]
    evidence.update({
        "stage": stage, "as_of_date": latest.period_end.isoformat(),
        "transition_date": transition_date, "limit_up_events": events,
        "limit_up_count": None if limit_dates is None else int(evidence["limit_up_count"]) + sum(
            b.period_end.isoformat() in limit_dates for b in follow
        ),
        "followup_sessions": len(follow),
    })
    return AccumulationPattern(
        pattern.score, pattern.start_date, pattern.end_date,
        pattern.lower, pattern.upper, evidence, stage,
    )
