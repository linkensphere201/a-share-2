from stock_harness.chat_context import MAX_CONTEXT_ITEMS, _bounded_analysis_items


def test_bounded_context_keeps_scenario_and_its_sources() -> None:
    ordinary = [
        {"item_id": f"item-{index}", "item_type": "evidence", "payload": {}}
        for index in range(MAX_CONTEXT_ITEMS + 20)
    ]
    ordinary[170]["item_id"] = "late-source"
    scenario = {
        "item_id": "scenario-1",
        "item_type": "scenario",
        "payload": {
            "evidence_item_ids": ["late-source"],
            "invalidation_evidence_item_ids": ["item-169"],
        },
    }

    bounded = _bounded_analysis_items([*ordinary, scenario])
    ids = [item["item_id"] for item in bounded]

    assert len(bounded) == MAX_CONTEXT_ITEMS
    assert ids[:3] == ["scenario-1", "item-169", "late-source"]
