from datetime import date, timedelta

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.analysis_results import GeneratedAnalysisItem, GeneratedItemType
from stock_harness.structural_scenario_engine import build_structural_scenario_items


def _bars(count: int = 40) -> list[AnalysisBar]:
    start = date(2026, 1, 1)
    result = []
    for index in range(count):
        close = 9.5 + index * 0.012
        result.append(AnalysisBar(
            period_start=start + timedelta(days=index),
            period_end=start + timedelta(days=index),
            open=close - 0.04,
            high=close + 0.18,
            low=close - 0.18,
            close=close,
            volume=1_000 + index,
            sources=("test",),
            contains_provisional=False,
            period_complete=True,
            observed_at_ms=index,
        ))
    return result


def _items() -> list[GeneratedAnalysisItem]:
    return [
        GeneratedAnalysisItem(
            "long-resistance", GeneratedItemType.LINE,
            {
                "kind": "resistance", "horizon": "long",
                "projected_price": 10.2, "score": 0.9,
                "first_pivot_date": "2026-01-02",
            },
        ),
        GeneratedAnalysisItem(
            "long-resistance-event", GeneratedItemType.EVIDENCE,
            {
                "kind": "latest-structural-event-summary",
                "current_state": "ready", "direction": "up",
                "boundary_price": 10.2, "invalidation_level": 10.2,
            },
            parent_item_id="long-resistance",
        ),
        GeneratedAnalysisItem(
            "support-zone", GeneratedItemType.ZONE,
            {
                "kind": "key-level", "lower": 9.2, "upper": 9.45,
                "score": 0.8,
            },
        ),
        GeneratedAnalysisItem(
            "target-zone-a", GeneratedItemType.ZONE,
            {
                "kind": "key-level", "lower": 11.8, "upper": 12.0,
                "score": 0.75,
            },
        ),
        GeneratedAnalysisItem(
            "target-zone-b", GeneratedItemType.ZONE,
            {
                "kind": "estimated-volume-at-price", "lower": 11.82,
                "upper": 12.1, "estimated_share": 0.16,
            },
        ),
        GeneratedAnalysisItem(
            "core-analysis-projection", GeneratedItemType.EVIDENCE,
            {
                "kind": "core-analysis-projection",
                "structural_item_ids": [
                    "long-resistance", "support-zone", "target-zone-a",
                    "target-zone-b",
                ],
            },
        ),
    ]


def test_builds_traceable_structural_scenario_and_clusters_targets() -> None:
    generated = build_structural_scenario_items(_bars(), _items())
    scenario = next(item for item in generated if item.item_type is GeneratedItemType.SCENARIO)
    payload = scenario.payload

    assert payload["contract_version"] == "structural-trade-scenario-v2"
    assert payload["direction"] == "long"
    assert payload["state"] == "waiting-trigger"
    assert payload["entry_price"] > 10.2
    assert payload["invalidation_price"] < payload["entry_price"]
    assert payload["risk_percent"] > 0
    targets = payload["targets"]
    assert isinstance(targets, list) and targets
    assert targets[0]["label"] == "T1"
    assert targets[0]["price"] == 11.8
    assert targets[0]["evidence_item_ids"] == ["target-zone-a", "target-zone-b"]
    assert targets[0]["risk_reward_ratio"] is not None
    assert targets[0]["stressed_risk_reward_ratio"] < targets[0]["risk_reward_ratio"]
    assert "long-resistance" in payload["evidence_item_ids"]
    assert payload["invalidation_evidence_item_ids"] == ["long-resistance"]


def test_range_references_exclude_latest_bar() -> None:
    bars = _bars()
    latest = bars[-1]
    bars[-1] = AnalysisBar(
        period_start=latest.period_start,
        period_end=latest.period_end,
        open=latest.open,
        high=99,
        low=1,
        close=latest.close,
        volume=latest.volume,
        sources=latest.sources,
        contains_provisional=False,
        period_complete=True,
        observed_at_ms=latest.observed_at_ms,
    )

    generated = build_structural_scenario_items(bars, _items())
    range_20 = next(item for item in generated if item.item_id == "structural-range-20")

    assert range_20.payload["high"] < 99
    assert range_20.payload["low"] > 1
