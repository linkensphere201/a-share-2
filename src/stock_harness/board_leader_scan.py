"""Daily-bar proxy ranking for current board dragon-one/dragon-two identities."""

from __future__ import annotations

from array import array
from dataclasses import dataclass
from math import isfinite, log1p, sqrt
from statistics import fmean
from typing import Sequence


ALGORITHM_VERSION = "board-dragon-daily-v1"


@dataclass(frozen=True)
class CompactReturns:
    dates: array
    returns: array


@dataclass(frozen=True)
class StockFeatures:
    symbol: str
    observations: int
    event_intensity: float
    peak_strength: float
    recent_strength: float
    persistence: float
    liquidity: float
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
    limit_days = sum(value >= 0.095 for value in daily)
    surge_days = sum(value >= 0.05 for value in daily)
    event_intensity = min(1.0, limit_days / 10.0 + surge_days / 40.0)
    peak_20 = _maximum_window_return(closes, 20)
    peak_60 = _maximum_window_return(closes, 60)
    peak_strength = _clip((peak_20 + peak_60 * 0.65) / 1.1)
    recent_strength = _clip(
        (_period_return(closes, 20) + _period_return(closes, 60) * 0.7
         + _period_return(closes, 120) * 0.45 + 0.55) / 1.8
    )
    persistence = _clip(
        sum(value > 0.02 for value in daily) / max(1, len(daily)) * 6.0
    )
    turnover_proxies = [
        float(item["close"]) * max(0.0, float(item.get("volume") or 0))
        for item in ordered[-60:]
    ]
    liquidity = log1p(fmean(turnover_proxies)) if turnover_proxies else 0.0
    return StockFeatures(
        symbol=symbol,
        observations=len(closes),
        event_intensity=event_intensity,
        peak_strength=peak_strength,
        recent_strength=recent_strength,
        persistence=persistence,
        liquidity=liquidity,
        returns=CompactReturns(ordinals, compact_returns),
    )


def rank_board_leaders(
    members: Sequence[StockFeatures],
    board_returns: CompactReturns,
) -> list[RankedLeader]:
    eligible = [item for item in members if item.observations >= 120]
    if len(eligible) < 4 or len(board_returns.returns) < 60:
        return []
    raw = {
        "event": [item.event_intensity for item in eligible],
        "peak": [item.peak_strength for item in eligible],
        "recent": [item.recent_strength for item in eligible],
        "persistence": [item.persistence for item in eligible],
        "liquidity": [item.liquidity for item in eligible],
        "association": [_positive_board_association(item.returns, board_returns) for item in eligible],
    }
    percentiles = {name: _percentile_scores(values) for name, values in raw.items()}
    scored: list[tuple[float, StockFeatures, dict[str, float]]] = []
    for index, item in enumerate(eligible):
        part = {name: values[index] for name, values in percentiles.items()}
        score = (
            part["event"] * 0.24 + part["peak"] * 0.18
            + part["recent"] * 0.14 + part["persistence"] * 0.10
            + part["liquidity"] * 0.18 + part["association"] * 0.16
        )
        scored.append((score, item, part))
    scored.sort(key=lambda item: (-item[0], item[1].symbol))
    if len(scored) < 2:
        return []
    separation = max(0.0, scored[0][0] - scored[2][0] if len(scored) > 2 else scored[0][0] - scored[1][0])
    coverage = min(1.0, len(eligible) / 12.0)
    result: list[RankedLeader] = []
    for index, (score, item, part) in enumerate(scored[:2], start=1):
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


def _positive_board_association(stock: CompactReturns, board: CompactReturns) -> float:
    left = right = 0
    stock_values: list[float] = []
    board_values: list[float] = []
    while left < len(stock.dates) and right < len(board.dates):
        if stock.dates[left] < board.dates[right]:
            left += 1
        elif stock.dates[left] > board.dates[right]:
            right += 1
        else:
            board_return = float(board.returns[right])
            if board_return > 0.01:
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


def _date_ordinal(value: str) -> int:
    compact = value[:10].replace("-", "")
    return int(compact)


def _clip(value: float) -> float:
    return min(1.0, max(0.0, value))
