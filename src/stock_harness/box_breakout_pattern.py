"""Causal breakout/retest of the shared platform geometry, not a trade forecast."""
from dataclasses import asdict, dataclass
from math import isfinite
from statistics import fmean

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.long_platform_pattern import LongPlatformConfig, detect_long_platform

STRATEGY_ID = 'platform-box-breakout'
ALGORITHM_VERSION = 'platform-box-breakout-v1'
KIND = 'box-breakout-range'
PLATFORM_CONFIG = LongPlatformConfig(minimum_sessions=20, recent_sessions=10,
                                     maximum_recent_range=.20, maximum_range_ratio=1.05,
                                     maximum_volume_ratio=1.20, minimum_boundary_touches=2)


@dataclass(frozen=True, slots=True)
class BoxBreakoutConfig:
    observation_sessions: int = 10
    minimum_break_percent: float = .01
    minimum_volume_ratio: float = 1.30
    maximum_extension: float = .12
    maximum_peak_extension: float = .18
    failure_tolerance: float = .02
    retest_touch_tolerance: float = .02
    retest_volume_ratio: float = .85
    maximum_retest_extension: float = .06
    maximum_break_age: int = 5


CONFIG = BoxBreakoutConfig()


def detect_box_breakout(bars: tuple[AnalysisBar, ...] | list[AnalysisBar]) -> dict[str, object] | None:
    bars = tuple(bars[-PLATFORM_CONFIG.maximum_sessions - 60 - CONFIG.observation_sessions:])
    if len(bars) < PLATFORM_CONFIG.minimum_sessions + 1:
        return None
    if any(not b.period_complete or b.contains_provisional or b.contains_roll_event or b.period_start > b.period_end
           or not all(isfinite(v) and v > 0 for v in (b.open, b.high, b.low, b.close, b.volume))
           or not b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high for b in bars):
        return None
    if any(a.period_end >= b.period_start for a, b in zip(bars, bars[1:])):
        return None
    latest = bars[-1]
    for index in range(max(PLATFORM_CONFIG.minimum_sessions, len(bars) - CONFIG.observation_sessions), len(bars)):
        launch = bars[index]
        before = bars[max(0, index - 20):index]
        volume_ratio = launch.volume / fmean(b.volume for b in before)
        # Necessary cheap checks precede the full shared platform search.
        if (launch.close < max(b.high for b in before) * (1 + CONFIG.minimum_break_percent)
                or launch.close <= launch.open or volume_ratio < CONFIG.minimum_volume_ratio
                or (launch.close - launch.low) / (launch.high - launch.low) < .60):
            continue
        platform = detect_long_platform(bars[:index], config=PLATFORM_CONFIG)
        if platform is None:
            continue
        upper, lower = platform['upper'], platform['lower']
        if launch.close < upper * (1 + CONFIG.minimum_break_percent):
            continue
        window = bars[index - platform['platform_sessions']:index]
        touches = [i for i, b in enumerate(window) if b.high >= upper - (upper - lower) * .10]
        after = bars[index:]
        if (any(b.close < upper * (1 - CONFIG.failure_tolerance) or b.low < lower for b in after)
                or max(b.high for b in after) > upper * (1 + CONFIG.maximum_peak_extension)
                or not upper <= latest.close <= upper * (1 + CONFIG.maximum_extension)):
            continue
        age = len(bars) - index - 1
        retest = (age > 0 and latest.low <= upper * (1 + CONFIG.retest_touch_tolerance)
                  and upper <= latest.close <= upper * (1 + CONFIG.maximum_retest_extension)
                  and latest.volume <= launch.volume * CONFIG.retest_volume_ratio)
        if age > CONFIG.maximum_break_age and not retest:
            continue
        score = min(100., platform['score'] * .65 + 20 * min(volume_ratio / 2, 1)
                    + (15 if retest else 10))
        return {
            **platform, 'kind': KIND, 'algorithm_version': ALGORITHM_VERSION,
            'display_name': 'Platform box breakout', 'stage': 'breakout-retest' if retest else 'broken-out',
            'screen_eligible': True, 'platform_end_date': platform['end_date'],
            'as_of_date': latest.period_end.isoformat(),
            'launch_date': launch.period_start.isoformat(), 'breakout_age_sessions': age,
            'breakout_volume_ratio': round(volume_ratio, 4),
            'retest_breakout_volume_ratio': round(latest.volume / launch.volume, 4),
            'breakout_distance_percent': round((latest.close / upper - 1) * 100, 4),
            'upper_touch_count': len(touches), 'latest_close': latest.close,
            'score': round(score, 4), 'platform_parameters': asdict(PLATFORM_CONFIG),
            'parameters': asdict(CONFIG),
        }
    return None
