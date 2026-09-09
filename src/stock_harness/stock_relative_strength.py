"""Causal lightweight relative-strength features for stock observation scans."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from math import isfinite, log1p, sqrt
from statistics import fmean, median

from stock_harness.models import StoredDailyBar


ALGORITHM_VERSION = "stock-relative-strength-v1"
SCORER_VERSION = "stock-independent-strength-v1"
MINIMUM_BARS = 120
LOOKBACK_BARS = 260


def analyze_relative_strength(
    symbol: str,
    bars: Sequence[StoredDailyBar],
    effective_date: date,
    *,
    market_references: Mapping[str, Sequence[StoredDailyBar]],
    board_references: Mapping[str, Sequence[StoredDailyBar]] | None = None,
) -> dict[str, object]:
    """Build one causal feature record; callers perform cross-sectional scoring."""
    visible = sorted(
        (bar for bar in bars if bar.trade_date <= effective_date),
        key=lambda bar: bar.trade_date,
    )
    if (
        len(visible) < MINIMUM_BARS
        or visible[-1].trade_date != effective_date
        or any(bar.close <= 0 or not isfinite(bar.close) for bar in visible)
    ):
        return {
            "symbol": symbol, "effective_date": effective_date.isoformat(),
            "coverage_state": "insufficient", "classification": "data-unavailable",
            "eligible": False, "metrics": {},
            "disqualifiers": _coverage_reasons(visible, effective_date),
            "algorithm_version": ALGORITHM_VERSION,
        }

    stock_returns = _daily_returns(visible)
    market_series = _reference_returns(market_references, effective_date)
    board_references = board_references or {}
    board_series = _reference_returns(board_references, effective_date)
    closes = [bar.close for bar in visible]
    volumes = [max(0, bar.volume) for bar in visible]
    stock_periods = {period: _period_return(closes, period) for period in (1, 5, 10, 20, 60)}
    market_periods = _composite_period_returns(market_references, effective_date)
    board_periods = _composite_period_returns(board_references, effective_date)
    market_excess = {
        period: _subtract(stock_periods[period], market_periods[period])
        for period in stock_periods
    }
    board_excess = {
        period: _subtract(stock_periods[period], board_periods[period])
        for period in stock_periods
    }
    residuals = _adjusted_residuals(stock_returns, market_series, board_series)
    residual_periods = {
        period: round(sum(residuals[-period:]), 6) if len(residuals) >= period else None
        for period in (1, 5, 10, 20, 60)
    }
    residual10 = residuals[-10:]
    persistence = (
        sum(value > 0 for value in residual10) / len(residual10)
        if residual10 else None
    )
    downside_resistance = _downside_resistance(
        stock_returns, market_series, board_series,
    )
    market_correlation20 = _aligned_correlation(stock_returns, market_series, 20)
    market_correlation60 = _aligned_correlation(stock_returns, market_series, 60)
    board_correlation20 = _aligned_correlation(stock_returns, board_series, 20)
    board_correlation60 = _aligned_correlation(stock_returns, board_series, 60)
    median_volume20 = median(volumes[-20:])
    volume_ratio20 = volumes[-1] / median_volume20 if median_volume20 > 0 else None
    turnover_proxy20 = fmean(
        bar.close * max(0, bar.volume) for bar in visible[-20:]
    )
    ma20 = fmean(closes[-20:])
    ma60 = fmean(closes[-60:])
    prior_high20 = max(closes[-21:-1])
    trend_state = (
        "up" if closes[-1] > ma20 > ma60
        else "down" if closes[-1] < ma20 < ma60
        else "mixed"
    )
    price_volume_confirmation = _price_volume_confirmation(
        stock_periods[5], volume_ratio20,
    )
    metrics = {
        "trade_date": effective_date.isoformat(),
        "close": round(closes[-1], 6),
        "returns": _rounded(stock_periods),
        "market_excess": _rounded(market_excess),
        "board_excess": _rounded(board_excess),
        "adjusted_residual": _rounded(residual_periods),
        "residual_positive_ratio10": _round(persistence),
        "downside_day_resistance": _round(downside_resistance),
        "market_correlation20": _round(market_correlation20),
        "market_correlation60": _round(market_correlation60),
        "board_correlation20": _round(board_correlation20),
        "board_correlation60": _round(board_correlation60),
        "correlation_change": _round(
            _subtract(_best_correlation(market_correlation20, board_correlation20),
                      _best_correlation(market_correlation60, board_correlation60))
        ),
        "volume_ratio20": _round(volume_ratio20),
        "turnover_proxy20_log": round(log1p(max(0.0, turnover_proxy20)), 6),
        "trend_state": trend_state,
        "above_prior_high20": closes[-1] > prior_high20,
        "price_volume_confirmation": round(price_volume_confirmation, 6),
        "market_reference_count": len(market_references),
        "board_reference_count": len(board_references),
    }
    classification = classify_relative_strength(metrics)
    eligible = classification in {
        "independent-advance", "counter-trend-resilience",
        "emerging-independent-move",
    }
    return {
        "symbol": symbol, "effective_date": effective_date.isoformat(),
        "coverage_state": "complete", "classification": classification,
        "eligible": eligible, "metrics": metrics, "disqualifiers": [],
        "algorithm_version": ALGORITHM_VERSION,
    }


def classify_relative_strength(metrics: Mapping[str, object]) -> str:
    residual = _mapping(metrics.get("adjusted_residual"))
    market = _mapping(metrics.get("market_excess"))
    board = _mapping(metrics.get("board_excess"))
    returns = _mapping(metrics.get("returns"))
    r1 = _number(residual.get("1"))
    r5, r20 = _number(residual.get("5")), _number(residual.get("20"))
    excess20 = _mean_available(_number(market.get("20")), _number(board.get("20")))
    persistence = _number(metrics.get("residual_positive_ratio10"))
    volume_ratio = _number(metrics.get("volume_ratio20"))
    latest_return = _number(returns.get("5"))
    return20 = _number(returns.get("20"))
    reference_return20 = (
        return20 - excess20
        if return20 is not None and excess20 is not None else None
    )

    if (
        r1 is not None and abs(r1) >= .045 and volume_ratio is not None
        and volume_ratio >= 2.2 and (persistence is None or persistence < .6)
        and (r20 is None or abs(r20) < abs(r1) * 1.7)
    ):
        return "one-session-event-anomaly"
    if r20 is not None and r20 >= .08 and r5 is not None and r5 <= 0:
        return "decaying-independent-move"
    if (
        r5 is not None and r5 >= .035 and (r20 is None or r20 < .08)
        and persistence is not None and persistence >= .6
    ):
        return "emerging-independent-move"
    if (
        reference_return20 is not None and reference_return20 <= -.04
        and latest_return is not None and latest_return >= -.01
        and r20 is not None and r20 >= .05
        and persistence is not None and persistence >= .6
    ):
        return "counter-trend-resilience"
    if (
        r20 is not None and r20 >= .08 and excess20 is not None and excess20 >= .06
        and persistence is not None and persistence >= .6
        and metrics.get("trend_state") == "up"
    ):
        return "independent-advance"
    if (
        r20 is not None and r20 <= -.08 and excess20 is not None and excess20 <= -.06
    ):
        return "independent-decline"
    best_correlation = _best_correlation(
        _number(metrics.get("market_correlation20")),
        _number(metrics.get("board_correlation20")),
    )
    if (
        best_correlation is not None and best_correlation >= .72
        and r5 is not None and abs(r5) <= .02
    ):
        return "resynchronized"
    return "neutral"


def score_relative_strength(records: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    """Cross-sectionally calibrate independent strength; low correlation has no points."""
    complete = [record for record in records if record.get("coverage_state") == "complete"]
    component_values: dict[str, list[float]] = {
        "adjusted_excess": [], "persistence": [], "downside_decoupling": [],
        "trend_structure": [], "price_volume": [], "liquidity": [],
    }
    for record in complete:
        metrics = _mapping(record.get("metrics"))
        residual = _mapping(metrics.get("adjusted_residual"))
        market = _mapping(metrics.get("market_excess"))
        board = _mapping(metrics.get("board_excess"))
        component_values["adjusted_excess"].append(_mean_or_zero(
            _number(residual.get("20")), _number(market.get("20")),
            _number(board.get("20")),
        ))
        component_values["persistence"].append(
            _number(metrics.get("residual_positive_ratio10")) or 0.0
        )
        component_values["downside_decoupling"].append(
            _number(metrics.get("downside_day_resistance")) or 0.0
        )
        component_values["trend_structure"].append(
            1.0 if metrics.get("trend_state") == "up" else
            0.35 if metrics.get("trend_state") == "mixed" else 0.0
        )
        component_values["price_volume"].append(
            _number(metrics.get("price_volume_confirmation")) or 0.0
        )
        component_values["liquidity"].append(
            _number(metrics.get("turnover_proxy20_log")) or 0.0
        )
    percentiles = {
        name: _percentile_ranks(values) for name, values in component_values.items()
    }
    weights = {
        "adjusted_excess": 30.0, "persistence": 20.0,
        "downside_decoupling": 15.0, "trend_structure": 15.0,
        "price_volume": 10.0, "liquidity": 10.0,
    }
    scored: list[dict[str, object]] = []
    for index, record in enumerate(complete):
        components = {
            name: round(percentiles[name][index] * weight, 2)
            for name, weight in weights.items()
        }
        total = round(sum(components.values()), 2)
        eligible = bool(record.get("eligible")) and total >= 60
        scored.append({
            **record, "score": total, "score_components": components,
            "score_version": SCORER_VERSION, "eligible": eligible,
        })
    scored.sort(key=lambda item: (-float(item["score"]), str(item["symbol"])))
    for rank, record in enumerate(scored, 1):
        record["rank"] = rank
    return scored


def _daily_returns(bars: Sequence[StoredDailyBar]) -> dict[date, float]:
    return {
        current.trade_date: current.close / previous.close - 1.0
        for previous, current in zip(bars, bars[1:])
        if previous.close > 0
    }


def _reference_returns(
    references: Mapping[str, Sequence[StoredDailyBar]], effective_date: date,
) -> dict[date, float]:
    by_date: dict[date, list[float]] = {}
    for bars in references.values():
        visible = sorted(
            (bar for bar in bars if bar.trade_date <= effective_date),
            key=lambda bar: bar.trade_date,
        )
        if not visible or visible[-1].trade_date != effective_date:
            continue
        for trade_date, value in _daily_returns(visible).items():
            by_date.setdefault(trade_date, []).append(value)
    return {trade_date: fmean(values) for trade_date, values in by_date.items()}


def _adjusted_residuals(
    stock: Mapping[date, float], market: Mapping[date, float], board: Mapping[date, float],
) -> list[float]:
    result = []
    for trade_date in sorted(stock):
        references = [series[trade_date] for series in (market, board) if trade_date in series]
        if references:
            result.append(stock[trade_date] - fmean(references))
    return result[-60:]


def _downside_resistance(
    stock: Mapping[date, float], market: Mapping[date, float], board: Mapping[date, float],
) -> float | None:
    values = []
    for trade_date in sorted(stock)[-60:]:
        references = [series[trade_date] for series in (market, board) if trade_date in series]
        if references and fmean(references) < 0:
            values.append(stock[trade_date] - fmean(references))
    return fmean(values) if len(values) >= 5 else None


def _aligned_correlation(
    stock: Mapping[date, float], reference: Mapping[date, float], limit: int,
) -> float | None:
    dates = [trade_date for trade_date in sorted(stock) if trade_date in reference][-limit:]
    if len(dates) < min(10, limit):
        return None
    left = [stock[value] for value in dates]
    right = [reference[value] for value in dates]
    left_mean, right_mean = fmean(left), fmean(right)
    covariance = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    denominator = sqrt(
        sum((value - left_mean) ** 2 for value in left)
        * sum((value - right_mean) ** 2 for value in right)
    )
    return covariance / denominator if denominator > 0 else 0.0


def _composite_period_returns(
    references: Mapping[str, Sequence[StoredDailyBar]], effective_date: date,
) -> dict[int, float | None]:
    result: dict[int, list[float]] = {period: [] for period in (1, 5, 10, 20, 60)}
    for bars in references.values():
        visible = sorted(
            (bar for bar in bars if bar.trade_date <= effective_date),
            key=lambda bar: bar.trade_date,
        )
        if not visible or visible[-1].trade_date != effective_date:
            continue
        closes = [bar.close for bar in visible]
        for period in result:
            value = _period_return(closes, period)
            if value is not None:
                result[period].append(value)
    return {
        period: fmean(values) if values else None for period, values in result.items()
    }


def _period_return(closes: Sequence[float], period: int) -> float | None:
    return closes[-1] / closes[-period - 1] - 1.0 if len(closes) > period else None


def _price_volume_confirmation(return5: float | None, volume_ratio: float | None) -> float:
    if return5 is None or volume_ratio is None:
        return 0.0
    if return5 > 0:
        return max(0.0, return5) * min(2.0, volume_ratio)
    return min(0.0, return5) * max(0.5, volume_ratio)


def _coverage_reasons(bars: Sequence[StoredDailyBar], effective_date: date) -> list[str]:
    reasons = []
    if len(bars) < MINIMUM_BARS:
        reasons.append("insufficient-history")
    if not bars or bars[-1].trade_date != effective_date:
        reasons.append("stale-through-effective-date")
    if any(bar.close <= 0 or not isfinite(bar.close) for bar in bars):
        reasons.append("invalid-close")
    return reasons


def _percentile_ranks(values: Sequence[float]) -> list[float]:
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
        rank = ((start + end - 1) / 2) / denominator
        for _, index in ordered[start:end]:
            result[index] = rank
        start = end
    return result


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and isfinite(float(value)) else None


def _subtract(left: float | None, right: float | None) -> float | None:
    return left - right if left is not None and right is not None else None


def _best_correlation(left: float | None, right: float | None) -> float | None:
    values = [value for value in (left, right) if value is not None]
    return max(values) if values else None


def _mean_available(*values: float | None) -> float | None:
    available = [value for value in values if value is not None]
    return fmean(available) if available else None


def _mean_or_zero(*values: float | None) -> float:
    return _mean_available(*values) or 0.0


def _rounded(values: Mapping[int, float | None]) -> dict[str, float | None]:
    return {str(key): _round(value) for key, value in values.items()}


def _round(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None
