"""Causal 20-session volume-accumulation screening policy."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import sqrt
from statistics import median
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar


STRATEGY_ID = "volume-accumulation-20d"
STRATEGY_VERSION = "volume-accumulation-20d-v3"
STATE = "accumulating"


@dataclass(frozen=True, slots=True)
class VolumeAccumulationConfig:
    window: int = 20
    baseline_window: int = 20
    min_total_volume_ratio: float = 1.35
    min_median_volume_ratio: float = 1.20
    min_elevated_sessions: int = 5
    min_supported_blocks: int = 3
    min_cluster_sessions: int = 2
    min_cluster_peak_ratio: float = 1.80
    min_cluster_average_ratio: float = 1.50
    max_cluster_age_sessions: int = 5
    min_cluster_window_volume_ratio: float = 0.75
    min_cluster_baseline_elevated_sessions: int = 2
    max_dominant_session_share: float = 0.16
    max_absolute_return_percent: float = 6.0
    max_close_range_percent: float = 15.0
    max_daily_volatility_percent: float = 3.5
    min_up_down_volume_ratio: float = 1.0


@dataclass(frozen=True, slots=True)
class VolumeAccumulationSignal:
    score: float
    evidence: dict[str, object]


def detect_volume_accumulation(
    bars: Sequence[AnalysisBar],
    *,
    limit_up_dates: frozenset[str] = frozenset(),
    config: VolumeAccumulationConfig = VolumeAccumulationConfig(),
) -> VolumeAccumulationSignal | None:
    """Return a signal when recent volume expands while price remains compressed."""
    required = config.window + config.baseline_window
    if len(bars) < required:
        return None
    baseline = list(bars[-required:-config.window])
    recent = list(bars[-config.window:])
    if any(item.period_end.isoformat() in limit_up_dates for item in recent):
        return None

    baseline_total = sum(item.volume for item in baseline)
    recent_total = sum(item.volume for item in recent)
    baseline_median = median(item.volume for item in baseline)
    recent_median = median(item.volume for item in recent)
    if baseline_total <= 0 or baseline_median <= 0 or recent_total <= 0:
        return None
    total_ratio = recent_total / baseline_total
    median_ratio = recent_median / baseline_median
    elevated_threshold = baseline_median * 1.35
    elevated = [item for item in recent if item.volume >= elevated_threshold]
    block_ratios = [
        median(item.volume for item in recent[index:index + 5]) / baseline_median
        for index in range(0, config.window, 5)
    ]
    supported_blocks = sum(value >= 1.15 for value in block_ratios)
    dominant_share = max(item.volume for item in recent) / recent_total
    all_bars = baseline + recent
    local_ratios = []
    for index in range(config.baseline_window, len(all_bars)):
        local_baseline = median(item.volume for item in all_bars[index - 10:index])
        local_ratios.append(all_bars[index].volume / max(local_baseline, 1))
    cluster_indices = [index for index, value in enumerate(local_ratios) if value >= 1.35]
    clusters: list[list[int]] = []
    for index in cluster_indices:
        if not clusters or index - clusters[-1][-1] > 2:
            clusters.append([index])
        else:
            clusters[-1].append(index)
    best_cluster = max(
        clusters,
        key=lambda values: (len(values), sum(local_ratios[index] for index in values)),
        default=[],
    )
    cluster_sessions = len(best_cluster)
    cluster_peak_ratio = max((local_ratios[index] for index in best_cluster), default=0.0)
    cluster_average_ratio = (
        sum(local_ratios[index] for index in best_cluster) / cluster_sessions
        if cluster_sessions else 0.0
    )
    cluster_age = config.window - 1 - best_cluster[-1] if best_cluster else config.window
    distributed_pile = (
        total_ratio >= config.min_total_volume_ratio
        and median_ratio >= config.min_median_volume_ratio
        and len(elevated) >= config.min_elevated_sessions
        and supported_blocks >= config.min_supported_blocks
    )
    clustered_pile = (
        cluster_sessions >= config.min_cluster_sessions
        and cluster_peak_ratio >= config.min_cluster_peak_ratio
        and cluster_average_ratio >= config.min_cluster_average_ratio
        and cluster_age <= config.max_cluster_age_sessions
        and total_ratio >= config.min_cluster_window_volume_ratio
        and len(elevated) >= config.min_cluster_baseline_elevated_sessions
    )

    first_close = recent[0].close
    closes = [item.close for item in recent]
    net_return = (recent[-1].close / first_close - 1) * 100
    close_range = (max(closes) / min(closes) - 1) * 100
    daily_returns = [
        (recent[index].close / recent[index - 1].close - 1) * 100
        for index in range(1, len(recent))
    ]
    daily_volatility = sqrt(sum(value * value for value in daily_returns) / len(daily_returns))
    up_volume = sum(
        recent[index].volume for index in range(1, len(recent))
        if recent[index].close >= recent[index - 1].close
    )
    down_volume = sum(
        recent[index].volume for index in range(1, len(recent))
        if recent[index].close < recent[index - 1].close
    )
    up_down_ratio = up_volume / max(down_volume, 1)

    if (
        not (distributed_pile or clustered_pile)
        or dominant_share > config.max_dominant_session_share
        or abs(net_return) > config.max_absolute_return_percent
        or close_range > config.max_close_range_percent
        or daily_volatility > config.max_daily_volatility_percent
        or up_down_ratio < config.min_up_down_volume_ratio
    ):
        return None

    volume_score = min(40.0, max(
        12 + max(0.0, total_ratio - 1) * 16 + max(0.0, median_ratio - 1) * 10,
        10 + min(15.0, max(0.0, cluster_peak_ratio - 1) * 4)
        + min(15.0, max(0.0, cluster_average_ratio - 1) * 10),
    ))
    persistence_score = min(25.0, max(
        len(elevated) * 2 + supported_blocks * 3,
        cluster_sessions * 2 + max(0, 8 - cluster_age)
        + max(0.0, total_ratio - 0.75) * 8,
    ))
    compression_score = max(0.0, 25 - abs(net_return) * 1.2 - close_range * 0.55)
    demand_score = min(10.0, 4 + max(0.0, up_down_ratio - 0.9) * 4)
    score = round(min(100.0, volume_score + persistence_score + compression_score + demand_score), 2)
    evidence = {
        "contract_version": STRATEGY_VERSION,
        "window": config.window,
        "baseline_window": config.baseline_window,
        "as_of_date": recent[-1].period_end.isoformat(),
        "latest_close": recent[-1].close,
        "window_start_date": recent[0].period_start.isoformat(),
        "total_volume_ratio": round(total_ratio, 4),
        "median_volume_ratio": round(median_ratio, 4),
        "elevated_sessions": len(elevated),
        "supported_blocks": supported_blocks,
        "pile_mode": "distributed" if distributed_pile else "clustered",
        "cluster_sessions": cluster_sessions,
        "cluster_peak_ratio": round(cluster_peak_ratio, 4),
        "cluster_average_ratio": round(cluster_average_ratio, 4),
        "cluster_age_sessions": cluster_age,
        "block_volume_ratios": [round(value, 4) for value in block_ratios],
        "dominant_session_share": round(dominant_share, 4),
        "return_20d_percent": round(net_return, 4),
        "close_range_20d_percent": round(close_range, 4),
        "daily_volatility_percent": round(daily_volatility, 4),
        "up_down_volume_ratio": round(up_down_ratio, 4),
        "limit_up_count": 0,
        "config": asdict(config),
    }
    return VolumeAccumulationSignal(score=score, evidence=evidence)
