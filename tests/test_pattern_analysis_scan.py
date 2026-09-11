from datetime import date, timedelta

from stock_harness.models import StoredDailyBar
from stock_harness.pattern_analysis import PatternAnalysisService


def _bars(count: int = 260) -> list[StoredDailyBar]:
    start = date(2025, 1, 1)
    return [
        StoredDailyBar(
            "BK001.DC", start + timedelta(days=index),
            30 - index * .05, 30.3 - index * .05,
            29.7 - index * .05, 30 - index * .05, 1_000 + index,
            "test", index,
        )
        for index in range(count)
    ]


def test_scan_profile_returns_bounded_shared_structure_features() -> None:
    result = PatternAnalysisService.scan_daily(_bars())

    assert result.atr5 > 0
    assert result.short_shape["state"] == "falling"
    assert result.medium_shape["state"] == "falling"
    assert result.descending_envelopes == {"3m": None, "6m": None, "1y": None}
    assert "deceleration_count" in result.downside_deceleration
