"""Observable low-base absorption evidence, not inferred account ownership."""
from dataclasses import asdict, dataclass
from math import isfinite
from statistics import fmean, median

STRATEGY_ID = "low-accumulation-platform"
ALGORITHM_VERSION = "low-accumulation-platform-v1"
KIND = "low-accumulation-range"


@dataclass(frozen=True)
class LowAccumulationConfig:
    context_sessions: int = 60
    platform_windows: tuple[int, ...] = (10, 15, 20, 30, 40, 60)
    min_prior_decline: float = .10
    max_context_position: float = .40
    max_range: float = .12
    max_close_drift: float = .06
    min_floor_ratio: float = 1.0
    min_volume_expansion: float = 1.15
    max_volume_expansion: float = 3.0
    min_volume_persistence: float = .60
    min_center_lift: float = .003
    max_center_lift: float = .06
    max_late_early_range: float = 1.15
    max_down_up_volume: float = .90
    max_dominant_share: float = .25
    min_retained_fraction: float = .60


CONFIG = LowAccumulationConfig()


def detect_low_accumulation(bars):
    bars = tuple(bars[-(CONFIG.context_sessions + max(CONFIG.platform_windows)):])
    if len(bars) < CONFIG.context_sessions + min(CONFIG.platform_windows):
        return None
    if any(b.contains_provisional or b.contains_roll_event or not b.period_complete
           or b.period_start != b.period_end
           or not all(isfinite(v) and v > 0 for v in (b.open, b.high, b.low, b.close, b.volume))
           or not b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high for b in bars):
        return None
    if any(a.period_end >= b.period_start for a, b in zip(bars, bars[1:])):
        return None
    matches = []
    for width in CONFIG.platform_windows:
        if len(bars) < CONFIG.context_sessions + width:
            continue
        prior, base = bars[-width-CONFIG.context_sessions:-width], bars[-width:]
        peak, floor = max(b.high for b in prior), min(b.low for b in (*prior, *base))
        decline = 1 - median(b.close for b in prior[-10:]) / max(b.high for b in prior[:40])
        position = (base[-1].close - floor) / max(peak - floor, 1e-12)
        if decline < CONFIG.min_prior_decline or position > CONFIG.max_context_position:
            continue
        lower, upper = min(b.low for b in base), max(b.high for b in base)
        early, late = base[:width//2], base[width//2:]
        range_ratio = upper / lower - 1
        drift = base[-1].close / base[0].close - 1
        floor_ratio = min(b.low for b in late) / min(b.low for b in early)
        early_range = max(b.high for b in early) / min(b.low for b in early) - 1
        late_range = max(b.high for b in late) / min(b.low for b in late) - 1
        contraction = late_range / max(early_range, 1e-12)
        centers = [(b.high+b.low+b.close)/3 for b in base]
        third = width // 3
        center_lift = fmean(centers[-third:]) / fmean(centers[:third]) - 1
        if (range_ratio > CONFIG.max_range or not -.02 <= drift <= CONFIG.max_close_drift
            or floor_ratio < CONFIG.min_floor_ratio or contraction > CONFIG.max_late_early_range
            or lower < min(b.low for b in prior[-10:])
            or not CONFIG.min_center_lift <= center_lift <= CONFIG.max_center_lift
            or not fmean(centers[:third]) <= fmean(centers[third:-third]) <= fmean(centers[-third:])
            or base[-1].close < min(b.low for b in early)
            or base[-1].close > max(b.high for b in base[:-1])):
            continue
        pairs = list(zip((prior[-1], *base[:-1]), base))
        up = [b.volume for a, b in pairs if b.close > a.close]
        down = [b.volume for a, b in pairs if b.close < a.close]
        if len(up) < 3 or len(down) < 3:
            continue
        pullback_ratio = fmean(down) / fmean(up)
        dominant = max(range(width), key=lambda i: base[i].volume)
        robust_up = [b.volume for i, (a, b) in enumerate(pairs) if i != dominant and b.close > a.close]
        robust_down = [b.volume for i, (a, b) in enumerate(pairs) if i != dominant and b.close < a.close]
        robust_ratio = fmean(robust_down) / fmean(robust_up)
        share = max(b.volume for b in base) / sum(b.volume for b in base)
        baseline_volume = median(b.volume for b in prior[-20:])
        expansion = fmean(b.volume for b in base) / baseline_volume
        median_expansion = median(b.volume for b in base) / baseline_volume
        volume_persistence = sum(b.volume >= baseline_volume * 1.1 for b in base) / width
        robust_expansion = fmean(b.volume for i, b in enumerate(base) if i != dominant) / baseline_volume
        persistence = sum(b.close > a.close and b.volume >= median(up) for a, b in pairs) / width
        if (pullback_ratio > CONFIG.max_down_up_volume or robust_ratio > 1
            or not CONFIG.min_volume_expansion <= expansion <= CONFIG.max_volume_expansion
            or median_expansion < 1.1 or robust_expansion < 1.1
            or volume_persistence < CONFIG.min_volume_persistence
            or median(down) > median(up) or share > CONFIG.max_dominant_share
            or persistence < .20
            or any(b.close / a.close - 1 < -.02 and b.volume > fmean(up) * 1.5 for a, b in pairs)):
            continue
        # Only fully observed three-session follow-ups contribute; never infer future retention.
        retention = []
        for i, (a, b) in enumerate(pairs[:-3]):
            if b.close / a.close - 1 >= .005 and b.volume >= median(up):
                retention.append(min(x.close for x in base[i+1:i+4]) >= a.close + .30 * (b.close - a.close))
        if len(retention) < 2 or fmean(retention) < CONFIG.min_retained_fraction:
            continue
        close_location = sum(b.volume * (b.close-b.low) / max(b.high-b.low, 1e-12) for b in base) / sum(b.volume for b in base)
        if close_location < .50:
            continue
        score = 100 * (.25 * fmean(retention) + .20 * (1-pullback_ratio)
                       + .20 * (1-range_ratio/CONFIG.max_range) + .15 * close_location
                       + .20 * volume_persistence)
        stamp = lambda b: b.period_end.isoformat()
        matches.append({
            "kind": KIND, "algorithm_version": ALGORITHM_VERSION, "display_name": "Low accumulation platform",
            "stage": "shape-match", "screen_eligible": True, "score": round(score, 4),
            "as_of_date": stamp(base[-1]), "launch_date": stamp(base[0]),
            "start_date": stamp(base[0]), "end_date": stamp(base[-1]), "platform_sessions": width,
            "lower": lower, "upper": upper, "center": (lower+upper)/2, "latest_close": base[-1].close,
            "decline_return_percent": -100*decline, "range_position": position,
            "platform_range_percent": 100*range_ratio, "platform_return_percent": 100*drift,
            "floor_lift_percent": 100*(floor_ratio-1), "volatility_contraction": contraction,
            "center_drift_percent": 100*center_lift, "platform_volume_ratio": expansion,
            "median_volume_expansion": median_expansion, "volume_persistence": volume_persistence,
            "robust_volume_expansion": robust_expansion,
            "pullback_volume_ratio": pullback_ratio, "robust_pullback_volume_ratio": robust_ratio,
            "dominant_session_share": share, "demand_persistence": persistence,
            "retained_advance_fraction": fmean(retention), "retention_events": len(retention),
            "volume_weighted_close_location": close_location,
            "parameters": {**asdict(CONFIG), "platform_windows": list(CONFIG.platform_windows)},
            "first_target_price": None, "first_risk_reward": None,
            "uncertainty": "Price-volume absorption proxy only; no identified institutional ownership or intent.",
        })
    # Prefer the longest currently valid base, not a cherry-picked high-scoring tail.
    return max(matches, key=lambda item: (item["platform_sessions"], item["score"])) if matches else None
