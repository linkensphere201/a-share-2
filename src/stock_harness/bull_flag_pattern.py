"""Causal ongoing bull flags, owned by shared daily pattern analysis."""

from dataclasses import asdict, dataclass
from math import isfinite
from statistics import fmean


ALGORITHM_VERSION = "bull-flag-consolidation-v2"
STRATEGY_ID = "bull-flag-consolidation"
KIND = "bull-flag-range"


@dataclass(frozen=True, slots=True)
class BullFlagConfig:
    min_launch_age: int = 7
    max_launch_age: int = 12
    min_pole_sessions: int = 2
    max_pole_sessions: int = 5
    min_launch_daily_gain: float = .01
    min_pole_gain: float = .08
    max_pole_gain: float = .15
    min_pole_close_retention: float = .65
    min_flag_sessions: int = 3
    max_flag_range: float = .08
    min_center_drift: float = -.04
    max_center_drift: float = .005
    min_center_slope: float = -.008
    max_center_slope: float = .001
    min_directional_decline: float = .015
    max_decline_efficiency: float = .80
    max_retracement: float = .60
    max_flag_pole_volume_ratio: float = .70
    max_late_early_volume_ratio: float = .85
    min_rolling_contraction_fraction: float = .60
    max_local_volume_expansion: float = 1.80


CONFIG = BullFlagConfig()


def _slope(values):
    center = (len(values) - 1) / 2
    return sum((i - center) * v for i, v in enumerate(values)) / sum(
        (i - center) ** 2 for i in range(len(values)))


def detect_bull_flag(bars):
    bars = tuple(bars[-80:])
    if len(bars) < CONFIG.min_launch_age + 2:
        return None
    if any(not b.period_complete or b.contains_provisional or b.contains_roll_event
           or not all(isfinite(v) and v > 0 for v in (b.open, b.high, b.low, b.close, b.volume))
           or not b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high for b in bars):
        return None
    if any(a.period_end >= b.period_start for a, b in zip(bars, bars[1:])):
        return None
    matches = []
    blocked_until = -1
    # Follow earlier poles too, so a later day inside a large rally cannot become a new 8-15% pole.
    for launch in range(1, len(bars) - CONFIG.min_launch_age):
        if launch <= blocked_until:
            continue
        origin = bars[launch - 1].close
        if bars[launch].close <= bars[launch].open or bars[launch].close < origin * (1 + CONFIG.min_launch_daily_gain):
            continue
        peak_index = launch
        for i in range(launch + 1, len(bars)):
            if bars[i].high <= bars[peak_index].high or bars[i].close < bars[i - 1].close:
                break
            peak_index = i
        blocked_until = peak_index
        if not CONFIG.min_launch_age <= len(bars) - 1 - launch <= CONFIG.max_launch_age:
            continue
        pole, flag = bars[launch:peak_index + 1], bars[peak_index + 1:]
        if not CONFIG.min_pole_sessions <= len(pole) <= CONFIG.max_pole_sessions or len(flag) < CONFIG.min_flag_sessions:
            continue
        peak = max(b.high for b in pole)
        gain = peak / origin - 1
        if not CONFIG.min_pole_gain - 1e-12 <= gain <= CONFIG.max_pole_gain + 1e-12:
            continue
        close_retention = (pole[-1].close - origin) / (peak - origin)
        if close_retention < CONFIG.min_pole_close_retention:
            continue
        floor = min(origin, *(b.low for b in pole))
        lower, upper = min(b.low for b in flag), max(b.high for b in flag)
        retracement = (peak - lower) / (peak - floor)
        if (lower < floor or upper > peak or upper / lower - 1 > CONFIG.max_flag_range
            or retracement > CONFIG.max_retracement):
            continue
        # Once the initial flag breaks upward, do not relabel its later return as ongoing.
        if any(flag[i].close > max(b.high for b in flag[:i]) for i in range(CONFIG.min_flag_sessions, len(flag))):
            continue
        centers = [(b.high + b.low + b.close) / 3 for b in flag]
        width = max(1, len(flag) // 3)
        center_drift = fmean(centers[-width:]) / fmean(centers[:width]) - 1
        if not CONFIG.min_center_drift <= center_drift <= CONFIG.max_center_drift:
            continue
        center_slope = _slope(centers) / fmean(centers)
        if not CONFIG.min_center_slope <= center_slope <= CONFIG.max_center_slope:
            continue
        close_drift = flag[-1].close / flag[0].close - 1
        close_path = sum(abs(b.close - a.close) for a, b in zip(flag, flag[1:]))
        close_efficiency = abs(flag[-1].close - flag[0].close) / close_path if close_path else 0.
        if close_drift <= -CONFIG.min_directional_decline and close_efficiency >= CONFIG.max_decline_efficiency:
            continue
        pole_volume = fmean(b.volume for b in pole)
        flag_volume = fmean(b.volume for b in flag)
        late_early = fmean(b.volume for b in flag[-width:]) / fmean(b.volume for b in flag[:width])
        rolling = [fmean((a.volume, b.volume)) for a, b in zip(flag, flag[1:])]
        contraction = sum(b < a for a, b in zip(rolling, rolling[1:])) / (len(rolling) - 1)
        max_local_expansion = max(flag[i].volume / fmean(b.volume for b in flag[max(0, i - 3):i])
                                  for i in range(2, len(flag)))
        if (flag_volume / pole_volume > CONFIG.max_flag_pole_volume_ratio
            or late_early > CONFIG.max_late_early_volume_ratio
            or contraction < CONFIG.min_rolling_contraction_fraction
            or _slope([b.volume for b in flag]) >= 0
            or flag[-1].volume > fmean(b.volume for b in flag[:max(1, len(flag) // 2)])
            or max(b.volume for b in flag) > pole_volume
            or max_local_expansion > CONFIG.max_local_volume_expansion):
            continue
        score = 40 + 25 * (1 - late_early) + 20 * contraction + 15 * (1 - retracement)
        stamp = lambda b: b.period_end.isoformat()
        matches.append({
            "kind": KIND, "display_name": "Bull flag consolidation", "algorithm_version": ALGORITHM_VERSION,
            "launch_type": "bull-flag", "stage": "pullback-observation", "screen_eligible": True,
            "score": round(score, 4), "as_of_date": stamp(bars[-1]), "launch_date": stamp(pole[0]),
            "peak_date": stamp(pole[-1]), "start_date": stamp(flag[0]), "end_date": stamp(flag[-1]),
            "launch_age_sessions": len(bars) - 1 - launch, "pole_sessions": len(pole),
            "flag_sessions": len(flag), "impulse_gain_percent": round(gain * 100, 4),
            "pole_close_retention": round(close_retention, 4),
            "flag_close_drift_percent": round(close_drift * 100, 4),
            "flag_directional_efficiency": round(close_efficiency, 4),
            "max_local_volume_expansion": round(max_local_expansion, 4),
            "impulse_origin_price": origin, "pole_low": floor, "peak_price": peak,
            "lower": lower, "upper": upper, "center": (lower + upper) / 2, "latest_close": bars[-1].close,
            "center_drift_percent": round(center_drift * 100, 4),
            "flag_range_percent": round((upper / lower - 1) * 100, 4),
            "retracement_ratio": round(retracement, 4), "flag_pole_volume_ratio": round(flag_volume / pole_volume, 4),
            "late_early_volume_ratio": round(late_early, 4), "rolling_contraction_fraction": round(contraction, 4),
            "parameters": asdict(CONFIG), "first_target_price": None, "first_risk_reward": None,
            "uncertainty": "Ongoing daily price/volume structure only; no entry or future return inference.",
        })
    return max(matches, key=lambda m: (m["score"], m["launch_date"])) if matches else None
