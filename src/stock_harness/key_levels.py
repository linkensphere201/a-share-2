"""Auditable horizontal levels and approximate daily volume-at-price zones."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import exp, log
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.trend_pivots import PricePivot


@dataclass(frozen=True, slots=True)
class KeyLevelConfig:
    cluster_tolerance_percent: float = 0.015
    minimum_observations: int = 2
    max_levels: int = 8
    volume_bins: int = 48
    volume_decay_half_life_bars: int = 120
    dense_bin_quantile: float = 0.75
    max_volume_zones: int = 6

    def validate(self) -> None:
        if not 0.002 <= self.cluster_tolerance_percent <= 0.1:
            raise ValueError("level cluster tolerance must be between 0.2% and 10%")
        if not 2 <= self.minimum_observations <= 10:
            raise ValueError("minimum level observations must be between 2 and 10")
        if not 1 <= self.max_levels <= 20:
            raise ValueError("maximum levels must be between 1 and 20")
        if not 12 <= self.volume_bins <= 200:
            raise ValueError("volume bins must be between 12 and 200")
        if self.volume_decay_half_life_bars < 10:
            raise ValueError("volume decay half-life must be at least 10 bars")
        if not 0.5 <= self.dense_bin_quantile <= 0.95:
            raise ValueError("dense-bin quantile must be between 0.5 and 0.95")


@dataclass(frozen=True, slots=True)
class HorizontalLevel:
    lower: float
    upper: float
    center: float
    observation_count: int
    sources: tuple[str, ...]
    evidence_dates: tuple[date, ...]
    latest_date: date
    score: float
    score_components: dict[str, float]
    role_reversal: bool
    volume_confluence: float
    method: str = "confirmed-pivot-gap-cluster-v1"


@dataclass(frozen=True, slots=True)
class VolumePriceZone:
    lower: float
    upper: float
    center: float
    estimated_volume: float
    estimated_share: float
    evidence_dates: tuple[date, ...]
    score: float
    uncertainty: str = "estimated from daily high-low uniform distribution"
    method: str = "daily-range-uniform-decayed-v1"


@dataclass(frozen=True, slots=True)
class VolumeProfile:
    zones: tuple[VolumePriceZone, ...]
    total_estimated_volume: float
    bin_width: float
    method: str = "daily-range-uniform-decayed-v1"


@dataclass(frozen=True, slots=True)
class _Observation:
    price: float
    observed_date: date
    source: str
    rejection: float


def detect_horizontal_levels(
    bars: Sequence[AnalysisBar],
    pivots: Sequence[PricePivot],
    config: KeyLevelConfig = KeyLevelConfig(),
    volume_zones: Sequence[VolumePriceZone] = (),
) -> tuple[HorizontalLevel, ...]:
    config.validate()
    if not bars:
        return ()
    ordered = sorted(bars, key=lambda item: item.period_end)
    bars_by_date = {bar.period_end: bar for bar in ordered}
    observations: list[_Observation] = []
    for pivot in pivots:
        if pivot.tentative or pivot.confirmed_date is None:
            continue
        bar = bars_by_date.get(pivot.pivot_date)
        if bar is None:
            continue
        bar_range = max(bar.high - bar.low, abs(bar.close) * 0.001)
        body_high = max(bar.open, bar.close)
        body_low = min(bar.open, bar.close)
        wick = bar.high - body_high if pivot.kind.value == "high" else body_low - bar.low
        observations.append(_Observation(
            pivot.price, pivot.pivot_date, f"pivot-{pivot.kind.value}",
            max(0.0, min(1.0, wick / bar_range)),
        ))
    for previous, current in zip(ordered, ordered[1:]):
        if current.low > previous.high:
            observations.extend((
                _Observation(previous.high, current.period_end, "gap-lower", 0.75),
                _Observation(current.low, current.period_end, "gap-upper", 0.75),
            ))
        elif current.high < previous.low:
            observations.extend((
                _Observation(current.high, current.period_end, "gap-lower", 0.75),
                _Observation(previous.low, current.period_end, "gap-upper", 0.75),
            ))
    if not observations:
        return ()

    clusters: list[list[_Observation]] = []
    for observation in sorted(observations, key=lambda item: item.price):
        if not clusters:
            clusters.append([observation])
            continue
        center = sum(item.price for item in clusters[-1]) / len(clusters[-1])
        tolerance = max(abs(center), abs(observation.price), 1e-9) * config.cluster_tolerance_percent
        if abs(observation.price - center) <= tolerance:
            clusters[-1].append(observation)
        else:
            clusters.append([observation])

    latest = ordered[-1].period_end
    total_span = max(1, (latest - ordered[0].period_end).days)
    levels: list[HorizontalLevel] = []
    for cluster in clusters:
        distinct_dates = sorted({item.observed_date for item in cluster})
        if len(distinct_dates) < config.minimum_observations:
            continue
        center = sum(item.price for item in cluster) / len(cluster)
        tolerance = abs(center) * config.cluster_tolerance_percent
        touch_score = min(1.0, len(distinct_dates) / 5)
        source_score = min(1.0, len({item.source for item in cluster}) / 3)
        source_set = {item.source for item in cluster}
        role_reversal = "pivot-high" in source_set and "pivot-low" in source_set
        volume_confluence = max((
            min(1.0, zone.estimated_share / 0.2)
            for zone in volume_zones
            if zone.lower - tolerance <= center <= zone.upper + tolerance
        ), default=0.0)
        recency = 1 - min(1.0, (latest - distinct_dates[-1]).days / total_span)
        rejection = sum(item.rejection for item in cluster) / len(cluster)
        score = (
            0.25 * touch_score + 0.10 * source_score + 0.20 * recency
            + 0.20 * rejection + 0.15 * volume_confluence
            + 0.10 * int(role_reversal)
        )
        levels.append(HorizontalLevel(
            lower=center - tolerance / 2,
            upper=center + tolerance / 2,
            center=center,
            observation_count=len(distinct_dates),
            sources=tuple(sorted({item.source for item in cluster})),
            evidence_dates=tuple(distinct_dates[-12:]),
            latest_date=distinct_dates[-1],
            score=round(score, 6),
            score_components={
                "touches": round(touch_score, 6),
                "source_diversity": round(source_score, 6),
                "recency": round(recency, 6),
                "rejection": round(rejection, 6),
                "volume_confluence": round(volume_confluence, 6),
                "role_reversal": float(role_reversal),
            },
            role_reversal=role_reversal,
            volume_confluence=round(volume_confluence, 6),
        ))
    levels.sort(key=lambda item: item.score, reverse=True)
    return tuple(levels[:config.max_levels])


def estimate_daily_volume_profile(
    bars: Sequence[AnalysisBar],
    config: KeyLevelConfig = KeyLevelConfig(),
) -> VolumeProfile:
    config.validate()
    ordered = sorted(bars, key=lambda item: item.period_end)
    positive = [bar for bar in ordered if bar.volume > 0 and bar.high >= bar.low]
    if not positive:
        return VolumeProfile((), 0.0, 0.0)
    lower = min(bar.low for bar in positive)
    upper = max(bar.high for bar in positive)
    if upper <= lower:
        return VolumeProfile((), 0.0, 0.0)
    bin_width = (upper - lower) / config.volume_bins
    volumes = [0.0] * config.volume_bins
    evidence: list[dict[date, float]] = [{} for _ in range(config.volume_bins)]
    decay_rate = log(2) / config.volume_decay_half_life_bars
    for age, bar in enumerate(reversed(positive)):
        weighted_volume = bar.volume * exp(-decay_rate * age)
        first_bin = max(0, min(config.volume_bins - 1, int((bar.low - lower) / bin_width)))
        last_bin = max(0, min(config.volume_bins - 1, int((bar.high - lower) / bin_width)))
        count = last_bin - first_bin + 1
        allocation = weighted_volume / count
        for index in range(first_bin, last_bin + 1):
            volumes[index] += allocation
            evidence[index][bar.period_end] = evidence[index].get(bar.period_end, 0.0) + allocation
    total = sum(volumes)
    nonzero = sorted(value for value in volumes if value > 0)
    threshold_index = min(len(nonzero) - 1, int((len(nonzero) - 1) * config.dense_bin_quantile))
    threshold = nonzero[threshold_index]
    dense_indexes = [index for index, value in enumerate(volumes) if value >= threshold]
    groups: list[list[int]] = []
    for index in dense_indexes:
        if groups and index == groups[-1][-1] + 1:
            groups[-1].append(index)
        else:
            groups.append([index])
    zones: list[VolumePriceZone] = []
    for group in groups:
        zone_volume = sum(volumes[index] for index in group)
        contributions: dict[date, float] = {}
        for index in group:
            for observed_date, value in evidence[index].items():
                contributions[observed_date] = contributions.get(observed_date, 0.0) + value
        evidence_dates = tuple(
            item[0] for item in sorted(
                contributions.items(), key=lambda item: item[1], reverse=True
            )[:12]
        )
        zone_lower = lower + group[0] * bin_width
        zone_upper = lower + (group[-1] + 1) * bin_width
        share = zone_volume / total if total else 0.0
        zones.append(VolumePriceZone(
            lower=zone_lower,
            upper=zone_upper,
            center=(zone_lower + zone_upper) / 2,
            estimated_volume=zone_volume,
            estimated_share=share,
            evidence_dates=evidence_dates,
            score=round(share, 6),
        ))
    zones.sort(key=lambda item: item.estimated_volume, reverse=True)
    return VolumeProfile(tuple(zones[:config.max_volume_zones]), total, bin_width)


def estimate_clear_space(
    latest_close: float,
    levels: Sequence[HorizontalLevel],
    zones: Sequence[VolumePriceZone],
) -> dict[str, float | None]:
    centers = sorted({item.center for item in levels} | {item.center for item in zones})
    lower = max((value for value in centers if value < latest_close), default=None)
    upper = min((value for value in centers if value > latest_close), default=None)
    return {
        "lower_price": lower,
        "lower_percent": (((latest_close - lower) / latest_close) * 100) if lower else None,
        "upper_price": upper,
        "upper_percent": ((upper / latest_close - 1) * 100) if upper else None,
    }
