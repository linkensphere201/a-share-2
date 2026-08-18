from stock_harness.analysis_results import GeneratedAnalysisItem, GeneratedItemType
from stock_harness.pattern_ranking import rank_pattern_candidates


def _pattern(
    item_id: str,
    start: str,
    end: str,
    score: float,
    *,
    state: str = "confirmed",
    horizon: str = "long",
) -> GeneratedAnalysisItem:
    return GeneratedAnalysisItem(
        item_id=item_id,
        item_type=GeneratedItemType.PATTERN,
        payload={
            "start_date": start,
            "end_date": end,
            "score": score,
            "completion_state": state,
            "horizon": horizon,
            "primary": True,
        },
    )


def test_ranks_overlapping_families_without_deleting_alternatives():
    items = [
        _pattern("double-bottom", "2026-01-01", "2026-01-20", 0.72),
        _pattern("triangle", "2026-01-05", "2026-01-18", 0.81),
        _pattern("old-flag", "2025-09-01", "2025-09-15", 0.99),
    ]

    ranked = rank_pattern_candidates(items)
    by_id = {item.item_id: item for item in ranked}

    assert len(ranked) == len(items)
    assert sum(item.payload["primary"] is True for item in ranked) == 1
    assert by_id["triangle"].payload["primary"] is True
    assert by_id["triangle"].payload["overlaps_with"] == ["double-bottom"]
    assert by_id["double-bottom"].payload["nested_with"] == ["triangle"]
    assert by_id["old-flag"].payload["overlap_group"] is None


def test_selects_one_primary_per_horizon_and_deprioritizes_invalidated():
    items = [
        _pattern("short-active", "2026-02-01", "2026-02-10", 0.6, horizon="short"),
        _pattern(
            "short-invalid", "2026-02-02", "2026-02-11", 1.0,
            state="invalidated", horizon="short",
        ),
        _pattern("long-active", "2025-01-01", "2026-02-01", 0.7),
    ]

    ranked = rank_pattern_candidates(items)
    primaries = {
        item.item_id for item in ranked if item.payload["primary"] is True
    }

    assert primaries == {"short-active", "long-active"}
    assert next(
        item for item in ranked if item.item_id == "short-invalid"
    ).payload["interpretation_rank"] == 2


def test_preserves_non_pattern_items_and_skips_malformed_pattern_dates():
    evidence = GeneratedAnalysisItem(
        "evidence", GeneratedItemType.EVIDENCE, {"kind": "test"}
    )
    malformed = GeneratedAnalysisItem(
        "malformed", GeneratedItemType.PATTERN, {"start_date": "bad"}
    )

    assert rank_pattern_candidates([evidence, malformed]) == [evidence, malformed]
