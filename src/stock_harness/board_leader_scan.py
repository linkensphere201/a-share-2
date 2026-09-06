"""Daily-bar proxy ranking for recent and historical board identities."""

from __future__ import annotations

from array import array
from dataclasses import dataclass
from math import isfinite, log1p, sqrt
from statistics import fmean
from typing import Sequence


ALGORITHM_VERSION = "board-recognition-dual-v3"
RECENT_PROFILE = "recent"
HISTORICAL_PROFILE = "historical"
PROFILE_WEIGHTS = {
    RECENT_PROFILE: {
        "event": 0.10,
        "peak": 0.08,
        "recent": 0.32,
        "persistence": 0.08,
        "liquidity": 0.22,
        "association": 0.20,
    },
    HISTORICAL_PROFILE: {
        "event": 0.20,
        "peak": 0.12,
        "regime": 0.24,
        "persistence": 0.10,
        "liquidity": 0.10,
        "association": 0.12,
        "longevity": 0.12,
    },
}


@dataclass(frozen=True)
class CompactReturns:
    dates: array
    returns: array


@dataclass(frozen=True)
class StockFeatures:
    symbol: str
    observations: int
    recent_event_intensity: float
    historical_event_intensity: float
    recent_peak_strength: float
    historical_peak_strength: float
    recent_strength: float
    recent_persistence: float
    historical_persistence: float
    recent_liquidity: float
    historical_liquidity: float
    historical_regimes: float
    longevity: float
    returns: CompactReturns


@dataclass(frozen=True)
class RankedLeader:
    symbol: str
    rank: int
    score: float
    confidence: float
    components: dict[str, float]


def calculate_stock_features(symbol: str, bars: Sequence[dict[str, object]]) -> StockFeatures | None:
    ordered = sorted(bars, key=lambda item: str(item["trade_date"]))
    closes = [float(item["close"]) for item in ordered]
    if len(closes) < 120 or any(value <= 0 or not isfinite(value) for value in closes):
        return None
    daily = [(closes[index] / closes[index - 1] - 1.0) for index in range(1, len(closes))]
    ordinals = array("I", (_date_ordinal(str(item["trade_date"])) for item in ordered[1:]))
    compact_returns = array("f", daily)
    recent_daily = daily[-244:]
    limit_days = sum(value >= 0.095 for value in daily)
    surge_days = sum(value >= 0.05 for value in daily)
    recent_limit_days = sum(value >= 0.095 for value in recent_daily)
    recent_surge_days = sum(value >= 0.05 for value in recent_daily)
    years = max(1.0, len(daily) / 244.0)
    historical_event_intensity = (limit_days * 2.0 + surge_days * 0.5) / years
    recent_event_intensity = recent_limit_days * 2.0 + recent_surge_days * 0.5
    recent_closes = closes[-245:]
    recent_peak_strength = (
        _maximum_window_return(recent_closes, 20)
        + _maximum_window_return(recent_closes, 60) * 0.65
    )
    historical_peak_strength = (
        _maximum_window_return(closes, 20)
        + _maximum_window_return(closes, 60) * 0.65
    )
    recent_strength = (
        _period_return(closes, 20) + _period_return(closes, 60) * 0.7
        + _period_return(closes, 120) * 0.45
    )
    recent_persistence = sum(value > 0.02 for value in recent_daily) / max(1, len(recent_daily))
    historical_persistence = sum(value > 0.02 for value in daily) / max(1, len(daily))
    recent_turnover = [
        float(item["close"]) * max(0.0, float(item.get("volume") or 0))
        for item in ordered[-60:]
    ]
    historical_turnover = [
        float(item["close"]) * max(0.0, float(item.get("volume") or 0))
        for item in ordered
    ]
    return StockFeatures(
        symbol=symbol,
        observations=len(closes),
        recent_event_intensity=recent_event_intensity,
        historical_event_intensity=historical_event_intensity,
        recent_peak_strength=recent_peak_strength,
        historical_peak_strength=historical_peak_strength,
        recent_strength=recent_strength,
        recent_persistence=recent_persistence,
        historical_persistence=historical_persistence,
        recent_liquidity=log1p(fmean(recent_turnover)) if recent_turnover else 0.0,
        historical_liquidity=log1p(fmean(historical_turnover)) if historical_turnover else 0.0,
        historical_regimes=_historical_regime_count(daily),
        longevity=min(10.0, years),
        returns=CompactReturns(ordinals, compact_returns),
    )


def rank_board_leaders(
    members: Sequence[StockFeatures],
    board_returns: CompactReturns,
    profile: str = RECENT_PROFILE,
    limit: int = 2,
) -> list[RankedLeader]:
    if profile not in PROFILE_WEIGHTS:
        raise ValueError(f"unknown recognition profile: {profile}")
    eligible = [item for item in members if item.observations >= 120]
    if len(eligible) < 4 or len(board_returns.returns) < 60:
        return []
    raw = ({
        "event": [item.recent_event_intensity for item in eligible],
        "peak": [item.recent_peak_strength for item in eligible],
        "recent": [item.recent_strength for item in eligible],
        "persistence": [item.recent_persistence for item in eligible],
        "liquidity": [item.recent_liquidity for item in eligible],
        "association": [
            _positive_board_association(item.returns, board_returns, max_observations=244)
            for item in eligible
        ],
    } if profile == RECENT_PROFILE else {
        "event": [item.historical_event_intensity for item in eligible],
        "peak": [item.historical_peak_strength for item in eligible],
        "regime": [item.historical_regimes for item in eligible],
        "persistence": [item.historical_persistence for item in eligible],
        "liquidity": [item.historical_liquidity for item in eligible],
        "association": [_positive_board_association(item.returns, board_returns) for item in eligible],
        "longevity": [item.longevity for item in eligible],
    })
    percentiles = {name: _percentile_scores(values) for name, values in raw.items()}
    scored: list[tuple[float, StockFeatures, dict[str, float]]] = []
    for index, item in enumerate(eligible):
        part = {name: values[index] for name, values in percentiles.items()}
        score = sum(part[name] * weight for name, weight in PROFILE_WEIGHTS[profile].items())
        scored.append((score, item, part))
    scored.sort(key=lambda item: (-item[0], item[1].symbol))
    if len(scored) < 2:
        return []
    separation = max(0.0, scored[0][0] - scored[2][0] if len(scored) > 2 else scored[0][0] - scored[1][0])
    coverage = min(1.0, len(eligible) / 12.0)
    result: list[RankedLeader] = []
    for index, (score, item, part) in enumerate(scored[:max(0, limit)], start=1):
        evidence_floor = min(part["association"], max(part["event"], part["peak"]), part["liquidity"])
        confidence = _clip(coverage * 0.45 + separation * 0.25 + evidence_floor * 0.30)
        result.append(RankedLeader(
            symbol=item.symbol,
            rank=index,
            score=round(score, 6),
            confidence=round(confidence, 6),
            components={key: round(value, 6) for key, value in part.items()},
        ))
    return result


def compact_returns(bars: Sequence[dict[str, object]]) -> CompactReturns:
    ordered = sorted(bars, key=lambda item: str(item["trade_date"]))
    dates = array("I")
    values = array("f")
    for previous, current in zip(ordered, ordered[1:]):
        previous_close = float(previous["close"])
        current_close = float(current["close"])
        if previous_close <= 0 or current_close <= 0:
            continue
        dates.append(_date_ordinal(str(current["trade_date"])))
        values.append(current_close / previous_close - 1.0)
    return CompactReturns(dates, values)


def is_risk_name(name: str) -> bool:
    compact = name.upper().replace(" ", "")
    return compact.startswith(("ST", "*ST", "S*ST", "退市"))


def _positive_board_association(
    stock: CompactReturns,
    board: CompactReturns,
    max_observations: int | None = None,
) -> float:
    left = right = 0
    stock_values: list[float] = []
    board_values: list[float] = []
    cutoff = (
        board.dates[max(0, len(board.dates) - max_observations)]
        if max_observations is not None and board.dates
        else None
    )
    while left < len(stock.dates) and right < len(board.dates):
        if stock.dates[left] < board.dates[right]:
            left += 1
        elif stock.dates[left] > board.dates[right]:
            right += 1
        else:
            board_return = float(board.returns[right])
            if board_return > 0.01 and (cutoff is None or board.dates[right] >= cutoff):
                stock_values.append(float(stock.returns[left]))
                board_values.append(board_return)
            left += 1
            right += 1
    if len(stock_values) < 20:
        return 0.0
    correlation = _correlation(stock_values, board_values)
    participation = sum(value > 0 for value in stock_values) / len(stock_values)
    excess = fmean(stock_values) - fmean(board_values)
    return _clip((correlation + 1.0) * 0.3 + participation * 0.25 + (excess + 0.03) * 5.0)


def _correlation(left: Sequence[float], right: Sequence[float]) -> float:
    left_mean = fmean(left)
    right_mean = fmean(right)
    covariance = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_variance = sum((value - left_mean) ** 2 for value in left)
    right_variance = sum((value - right_mean) ** 2 for value in right)
    denominator = sqrt(left_variance * right_variance)
    return covariance / denominator if denominator > 0 else 0.0


def _percentile_scores(values: Sequence[float]) -> list[float]:
    if not values:
        return []
    ordered = sorted((value, index) for index, value in enumerate(values))
    result = [0.0] * len(values)
    denominator = max(1, len(values) - 1)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][0] == ordered[start][0]:
            end += 1
        rank = ((start + end - 1) / 2.0) / denominator
        for _, original_index in ordered[start:end]:
            result[original_index] = rank
        start = end
    return result


def _maximum_window_return(closes: Sequence[float], window: int) -> float:
    if len(closes) <= window:
        return 0.0
    return max(closes[index] / closes[index - window] - 1.0 for index in range(window, len(closes)))


def _period_return(closes: Sequence[float], window: int) -> float:
    if len(closes) <= window:
        return 0.0
    return closes[-1] / closes[-1 - window] - 1.0


def _historical_regime_count(daily: Sequence[float], window: int = 60) -> float:
    """Count independent windows with a recognisable acceleration event."""
    regimes = []
    for start in range(0, len(daily), window):
        values = daily[start : start + window]
        if len(values) < window // 2:
            continue
        limit_days = sum(value >= 0.095 for value in values)
        surge_days = sum(value >= 0.05 for value in values)
        regimes.append(limit_days >= 1 or surge_days >= 2)
    return float(sum(regimes))


def _date_ordinal(value: str) -> int:
    compact = value[:10].replace("-", "")
    return int(compact)


def _clip(value: float) -> float:
    return min(1.0, max(0.0, value))
