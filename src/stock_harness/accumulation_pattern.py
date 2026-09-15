"""Causal detection of a demand-led accumulation range after a sustained decline."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar


ALGORITHM_VERSION = "decline-platform-accumulation-v1"


@dataclass(frozen=True, slots=True)
class AccumulationPatternConfig:
    decline_window: int = 60
    lift_window: int = 10
    platform_window: int = 10
    min_decline_percent: float = 10.0
    min_ma20_decline_percent: float = 4.0
    min_ma_divergence_percent: float = 2.0
    min_bottom_lift_percent: float = 4.0
    max_bottom_lift_percent: float = 12.0
    max_platform_range_percent: float = 14.0
    max_platform_return_percent: float = 10.0
    max_small_body_percent: float = 3.0
    min_small_body_sessions: int = 7
    min_up_down_volume_ratio: float = 1.10
    min_platform_volume_ratio: float = 1.10
    max_dominant_session_share: float = 0.30


@dataclass(frozen=True, slots=True)
class AccumulationPattern:
    score: float
    start_date: str
    end_date: str
    lower: float
    upper: float
    evidence: dict[str, object]


def detect_accumulation_pattern(
    bars: Sequence[AnalysisBar],
    *,
    limit_up_dates: frozenset[str] | None = None,
    config: AccumulationPatternConfig = AccumulationPatternConfig(),
) -> AccumulationPattern | None:
    """Return a range only when decline, lift, platform, and demand all agree."""
    required = config.decline_window + config.lift_window + config.platform_window
    if len(bars) < required:
        return None
    decline = list(bars[-required:-(config.lift_window + config.platform_window)])
    lift = list(bars[-(config.lift_window + config.platform_window):-config.platform_window])
    platform = list(bars[-config.platform_window:])

    decline_return = (decline[-1].close / decline[0].close - 1) * 100
    early_ma20 = fmean(item.close for item in decline[:20])
    ma20 = fmean(item.close for item in decline[-20:])
    ma40 = fmean(item.close for item in decline[-40:])
    ma60 = fmean(item.close for item in decline[-60:])
    ma20_decline = (ma20 / early_ma20 - 1) * 100
    ma_divergence = (ma60 / ma20 - 1) * 100
    downward_stack = ma20 < ma40 < ma60

    half = max(1, config.lift_window // 2)
    early_bottom = fmean(item.low for item in lift[:half])
    late_bottom = fmean(item.low for item in lift[-half:])
    bottom_lift = (late_bottom / early_bottom - 1) * 100
    support_held = min(item.low for item in platform) >= min(item.low for item in lift) * 1.02

    lower = min(item.low for item in platform)
    upper = max(item.high for item in platform)
    platform_range = (upper / lower - 1) * 100
    platform_return = (platform[-1].close / platform[0].close - 1) * 100
    small_body_sessions = sum(
        abs(item.close / item.open - 1) * 100 <= config.max_small_body_percent
        for item in platform if item.open > 0
    )
    up_volume = sum(item.volume for item in platform if item.close >= item.open)
    down_volume = sum(item.volume for item in platform if item.close < item.open)
    up_down_ratio = up_volume / max(down_volume, 1)
    platform_volume = fmean(item.volume for item in platform)
    prior_volume = fmean(item.volume for item in [*decline[-10:], *lift])
    platform_volume_ratio = platform_volume / max(prior_volume, 1)
    dominant_share = max(item.volume for item in platform) / max(
        sum(item.volume for item in platform), 1,
    )
    limit_up_count = None if limit_up_dates is None else sum(
        item.period_end.isoformat() in limit_up_dates for item in [*lift, *platform]
    )

    if not (
        decline_return <= -config.min_decline_percent
        and ma20_decline <= -config.min_ma20_decline_percent
        and downward_stack
        and ma_divergence >= config.min_ma_divergence_percent
        and config.min_bottom_lift_percent <= bottom_lift <= config.max_bottom_lift_percent
        and support_held
        and platform_range <= config.max_platform_range_percent
        and abs(platform_return) <= config.max_platform_return_percent
        and small_body_sessions >= config.min_small_body_sessions
        and up_down_ratio >= config.min_up_down_volume_ratio
        and platform_volume_ratio >= config.min_platform_volume_ratio
        and dominant_share <= config.max_dominant_session_share
    ):
        return None

    decline_score = min(25.0, 10 + abs(decline_return) * .45 + abs(ma20_decline) * .35)
    lift_score = max(0.0, 25 - abs(bottom_lift - 7.0) * 2.5)
    platform_score = max(0.0, 25 - platform_range * .8 + small_body_sessions * 1.2)
    demand_score = min(
        25.0,
        8 + max(0.0, up_down_ratio - 1) * 6
        + max(0.0, platform_volume_ratio - 1) * 12,
    )
    score = round(min(100.0, decline_score + lift_score + platform_score + demand_score), 2)
    evidence = {
        "algorithm_version": ALGORITHM_VERSION,
        "as_of_date": platform[-1].period_end.isoformat(),
        "decline_start_date": decline[0].period_start.isoformat(),
        "decline_end_date": decline[-1].period_end.isoformat(),
        "decline_return_percent": round(decline_return, 4),
        "ma20_decline_percent": round(ma20_decline, 4),
        "ma_divergence_percent": round(ma_divergence, 4),
        "downward_ma_stack": downward_stack,
        "bottom_lift_percent": round(bottom_lift, 4),
        "support_held": support_held,
        "platform_range_percent": round(platform_range, 4),
        "platform_return_percent": round(platform_return, 4),
        "small_body_sessions": small_body_sessions,
        "up_volume": up_volume,
        "down_volume": down_volume,
        "up_down_volume_ratio": round(up_down_ratio, 4),
        "platform_volume_ratio": round(platform_volume_ratio, 4),
        "dominant_session_share": round(dominant_share, 4),
        "limit_up_count": limit_up_count,
        "limit_up_policy": "accepted-not-required",
        "config": asdict(config),
    }
    return AccumulationPattern(
        score=score,
        start_date=platform[0].period_start.isoformat(),
        end_date=platform[-1].period_end.isoformat(),
        lower=lower,
        upper=upper,
        evidence=evidence,
    )
