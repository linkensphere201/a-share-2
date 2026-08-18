from datetime import date, timedelta

from stock_harness.analysis_inputs import AnalysisBar, AnalysisTimeframe
from stock_harness.analysis_results import GeneratedItemType, validate_items
from stock_harness.generated_patterns import generate_pattern_items
from stock_harness.pattern_ranking import rank_pattern_candidates
from stock_harness.trend_lines_analysis import TrendHorizon
from stock_harness.trend_pivots import PivotKind, PricePivot


def _bars() -> list[AnalysisBar]:
    start = date(2026, 1, 1)
    closes = [10, 10.5, 11, 12, 11, 10.5, 10.1, 11, 12, 12.5]
    return [
        AnalysisBar(
            start + timedelta(days=index), start + timedelta(days=index),
            close, close + 0.3, close - 0.3, close,
            200 if index == 9 else 100, ("test",), False, True, index,
        )
        for index, close in enumerate(closes)
    ]


def _pivots() -> list[PricePivot]:
    start = date(2026, 1, 1)
    return [
        PricePivot(PivotKind.LOW, start, 9.7, start + timedelta(days=1), 0.5, False),
        PricePivot(
            PivotKind.HIGH, start + timedelta(days=3), 12.3,
            start + timedelta(days=4), 0.5, False,
        ),
        PricePivot(
            PivotKind.LOW, start + timedelta(days=6), 9.8,
            start + timedelta(days=7), 0.5, False,
        ),
    ]


def test_generates_horizon_qualified_patterns_and_child_evidence():
    bars, pivots = _bars(), _pivots()
    items = []
    for horizon in (TrendHorizon.SHORT, TrendHorizon.LONG):
        items.extend(generate_pattern_items(
            bars, pivots, horizon, AnalysisTimeframe.DAILY, preview=False
        ))
    ranked = rank_pattern_candidates(items)
    validate_items(ranked)
    patterns = [
        item for item in ranked if item.item_type is GeneratedItemType.PATTERN
    ]

    assert {item.payload["horizon"] for item in patterns} == {"short", "long"}
    pattern_ids = {item.item_id for item in patterns}
    assert {
        "short-pattern-double-bottom-0",
        "long-pattern-double-bottom-0",
    } <= pattern_ids
    assert sum(item.payload["primary"] is True for item in patterns) == 2
    for horizon in ("short", "long"):
        ranks = sorted(
            item.payload["interpretation_rank"] for item in patterns
            if item.payload["horizon"] == horizon
        )
        assert ranks == list(range(1, len(ranks) + 1))
    parent_ids = {
        item.parent_item_id for item in ranked if item.parent_item_id is not None
    }
    assert parent_ids <= pattern_ids
    assert {
        "short-pattern-double-bottom-0",
        "long-pattern-double-bottom-0",
    } <= parent_ids
