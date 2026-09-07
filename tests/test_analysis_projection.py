from stock_harness.analysis_projection import (
    build_core_projection_item,
    read_core_structural_item_ids,
)
from stock_harness.analysis_results import GeneratedAnalysisItem, GeneratedItemType


def _item(item_id: str, item_type: GeneratedItemType, **payload: object) -> GeneratedAnalysisItem:
    return GeneratedAnalysisItem(item_id, item_type, payload)


def test_core_projection_selects_each_horizon_role_and_primary_pattern() -> None:
    items = [
        _item("short-support-low", GeneratedItemType.LINE, kind="support", horizon="short", score=.4, touch_count=5),
        _item("short-support", GeneratedItemType.LINE, kind="support", horizon="short", score=.8, touch_count=2),
        _item("medium-resistance", GeneratedItemType.LINE, kind="resistance", horizon="medium", score=.7, touch_count=3),
        _item("pattern-alt", GeneratedItemType.PATTERN, horizon="medium", primary=False, interpretation_rank=2),
        _item("pattern-core", GeneratedItemType.PATTERN, horizon="medium", primary=True, interpretation_rank=1),
        _item("level", GeneratedItemType.ZONE, kind="key-level", score=.9),
    ]

    projection = build_core_projection_item(items)

    assert projection.payload["line_item_ids"] == ["medium-resistance", "short-support"]
    assert projection.payload["pattern_item_ids"] == ["pattern-core"]
    assert projection.payload["zone_item_ids"] == ["level"]


def test_projection_reader_rejects_missing_references() -> None:
    items = [
        {"item_id": "line", "item_type": "line", "payload": {}},
        {
            "item_id": "core-analysis-projection",
            "item_type": "evidence",
            "payload": {"structural_item_ids": ["line", "missing", 3]},
        },
    ]

    assert read_core_structural_item_ids(items) == ("line",)
