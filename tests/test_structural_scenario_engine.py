from datetime import date, timedelta

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.analysis_results import GeneratedAnalysisItem, GeneratedItemType
from stock_harness.structural_scenario_engine import (
    build_structural_scenario_items,
    project_scenario_summary,
)
from stock_harness.overhead_supply import build_overhead_supply_item


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


def test_retest_policy_and_projected_line_target_are_explicit() -> None:
    items = [
        GeneratedAnalysisItem("triangle", GeneratedItemType.PATTERN, {
            "pattern_type": "triangle", "direction": "bullish",
            "horizon": "medium", "neckline_price": 10.1,
            "invalidation_price": 9.4, "score": 0.9, "primary": True,
            "start_date": "2026-01-03",
        }),
        GeneratedAnalysisItem(
            "triangle-event", GeneratedItemType.EVIDENCE,
            {
                "kind": "breakout-state-summary", "current_state": "retesting",
                "direction": "up", "boundary_price": 10.1,
                "invalidation_level": 9.4,
            }, parent_item_id="triangle",
        ),
        GeneratedAnalysisItem("support", GeneratedItemType.ZONE, {
            "kind": "key-level", "lower": 9.2, "upper": 9.5, "score": 0.8,
        }),
        GeneratedAnalysisItem("future-resistance", GeneratedItemType.LINE, {
            "kind": "resistance", "horizon": "medium", "projected_price": 11.5,
            "slope_per_bar": 0.03, "score": 0.8,
        }),
        GeneratedAnalysisItem("core-analysis-projection", GeneratedItemType.EVIDENCE, {
            "kind": "core-analysis-projection",
            "structural_item_ids": ["triangle", "support", "future-resistance"],
        }),
    ]

    generated = build_structural_scenario_items(_bars(), items)
    payload = next(
        item.payload for item in generated if item.item_type is GeneratedItemType.SCENARIO
    )

    assert payload["state"] == "retest"
    assert payload["entry_policy"] == "observed-retest-hold"
    assert "touched the broken boundary" in payload["confirmation_rule"]
    assert any(
        target["basis"] == "trend-line-projection-10d"
        for target in payload["targets"]
    )


def test_overhead_supply_is_separate_uncertain_evidence() -> None:
    items = [
        GeneratedAnalysisItem("volume-overhead", GeneratedItemType.ZONE, {
            "kind": "estimated-volume-at-price", "lower": 10.5, "upper": 11,
            "estimated_share": 0.25, "evidence_dates": ["2026-01-30"],
        }),
        GeneratedAnalysisItem("failed-break", GeneratedItemType.EVIDENCE, {
            "kind": "breakout-state-summary", "current_state": "failed",
            "direction": "up",
        }),
    ]

    supply = build_overhead_supply_item(_bars(), items)

    assert supply.item_id == "overhead-supply-proxy"
    assert supply.payload["score"] > 0.5
    assert supply.payload["failed_upward_attempts"] == 1
    assert supply.payload["turnover_available"] is False
    assert "not actual holder cost" in supply.payload["uncertainty"]


def test_projection_can_select_long_scenario_when_primary_scenario_is_short() -> None:
    items = [{
        "item_id": "short-primary", "item_type": "scenario", "payload": {
            "kind": "structural-trade-scenario", "primary": True, "rank": 1,
            "direction": "short", "state": "triggered", "targets": [],
        },
    }, {
        "item_id": "long-secondary", "item_type": "scenario", "payload": {
            "kind": "structural-trade-scenario", "primary": False, "rank": 2,
            "direction": "long", "state": "retest", "targets": [],
        },
    }]

    projected = project_scenario_summary(items, direction="long")

    assert projected["scenario_item_id"] == "long-secondary"
    assert projected["direction"] == "long"
