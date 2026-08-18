"""Optional market and board context evidence for trend analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from stock_harness.analysis_inputs import AnalysisBar


@dataclass(frozen=True, slots=True)
class TrendContextSeries:
    symbol: str
    name: str
    kind: str
    bars: tuple[AnalysisBar, ...]


def build_trend_context_evidence(
    subject: TrendContextSeries,
    market: TrendContextSeries | None,
    related: Sequence[TrendContextSeries],
    *,
    unavailable: Sequence[dict[str, str]] = (),
) -> dict[str, object]:
    subject_summary = _series_summary(subject)
    market_summary = _series_summary(market) if market is not None else None
    related_summaries = [_series_summary(item) for item in related]
    weighted: list[tuple[float, float]] = []
    if subject_summary["available"]:
        weighted.append((0.5, float(subject_summary["trend_score"])))
    if market_summary is not None and market_summary["available"]:
        weighted.append((0.2, float(market_summary["trend_score"])))
    available_related = [item for item in related_summaries if item["available"]]
    if available_related:
        related_weight = 0.3 / len(available_related)
        weighted.extend(
            (related_weight, float(item["trend_score"]))
            for item in available_related
        )
    total_weight = sum(weight for weight, _score in weighted)
    combined = (
        sum(weight * score for weight, score in weighted) / total_weight
        if total_weight else 0.0
    )
    alignment = "supportive" if combined >= 0.25 else "adverse" if combined <= -0.25 else "mixed"
    return {
        "kind": "market-board-trend-context",
        "subject": subject_summary,
        "market": market_summary,
        "related": related_summaries,
        "unavailable": list(unavailable),
        "available_context_count": (
            int(market_summary is not None and market_summary["available"])
            + len(available_related)
        ),
        "combined_score": round(combined, 6),
        "alignment": alignment,
        "weights": {
            "subject": 0.5,
            "market": 0.2,
            "related_total": 0.3,
            "renormalized_over_available": True,
        },
        "analytical_only": True,
        "uncertainty": (
            "daily close trend context; mappings and histories may be incomplete"
        ),
    }


def _series_summary(value: TrendContextSeries) -> dict[str, object]:
    bars = sorted(value.bars, key=lambda item: item.period_end)
    if len(bars) < 2:
        return {
            "symbol": value.symbol,
            "name": value.name,
            "context_kind": value.kind,
            "available": False,
            "reason": "fewer than two visible bars",
        }
    latest = bars[-1]
    short_start = bars[max(0, len(bars) - 21)].close
    medium_start = bars[max(0, len(bars) - 61)].close
    return_short = latest.close / short_start - 1 if short_start else 0.0
    return_medium = latest.close / medium_start - 1 if medium_start else 0.0
    short_average = sum(item.close for item in bars[-20:]) / min(20, len(bars))
    medium_average = sum(item.close for item in bars[-60:]) / min(60, len(bars))
    return_score = _bounded(return_short / 0.10, -1.0, 1.0)
    medium_score = _bounded(return_medium / 0.20, -1.0, 1.0)
    average_score = (
        (0.2 if latest.close >= short_average else -0.2)
        + (0.2 if latest.close >= medium_average else -0.2)
    )
    score = 0.35 * return_score + 0.25 * medium_score + average_score
    return {
        "symbol": value.symbol,
        "name": value.name,
        "context_kind": value.kind,
        "available": True,
        "as_of_date": latest.period_end.isoformat(),
        "bar_count": len(bars),
        "return_20": round(return_short, 6),
        "return_60": round(return_medium, 6),
        "above_ma20": latest.close >= short_average,
        "above_ma60": latest.close >= medium_average,
        "trend_score": round(_bounded(score, -1.0, 1.0), 6),
    }


def _bounded(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
