from datetime import date, timedelta

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.trend_context import (
    TrendContextSeries,
    build_trend_context_evidence,
)


def _series(symbol: str, direction: float, kind: str = "board") -> TrendContextSeries:
    start = date(2026, 1, 1)
    bars = tuple(
        AnalysisBar(
            start + timedelta(days=index), start + timedelta(days=index),
            100 + direction * index, 101 + direction * index,
            99 + direction * index, 100 + direction * index,
            100, ("test",), False, True, index,
        )
        for index in range(30)
    )
    return TrendContextSeries(symbol, symbol, kind, bars)


def test_combines_subject_market_and_related_daily_trends():
    result = build_trend_context_evidence(
        _series("STOCK", 1.0, "subject"),
        _series("MARKET", 0.5, "market"),
        [_series("BOARD", 0.8), _series("CUSTOM", -0.2, "custom-index")],
    )

    assert result["available_context_count"] == 3
    assert result["combined_score"] > 0.25
    assert result["alignment"] == "supportive"
    assert result["related"][1]["context_kind"] == "custom-index"


def test_missing_context_renormalizes_to_subject_without_failure():
    unavailable = TrendContextSeries("EMPTY", "Empty", "board", ())
    result = build_trend_context_evidence(
        _series("STOCK", -1.0, "subject"), None, [unavailable],
        unavailable=[{"symbol": "MISSING", "reason": "no stored history"}],
    )

    assert result["available_context_count"] == 0
    assert result["alignment"] == "adverse"
    assert result["related"][0]["available"] is False
    assert result["unavailable"][0]["symbol"] == "MISSING"
