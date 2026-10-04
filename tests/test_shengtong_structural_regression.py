"""Read-only MCP snapshot; never query the production database in regression."""

from datetime import date
import json
from pathlib import Path
import pytest

from stock_harness.analysis_inputs import (
    AnalysisBar, AnalysisHorizons, AnalysisInput, AnalysisInputMode,
    AnalysisInstrumentContext, AnalysisTimeframe,
    AnalysisInputWarning,
)
from stock_harness.trend_analysis import _generated_items
from stock_harness.trend_pivots import DirectionalChangeConfig


def _input(cutoff: str, fixture_name: str = "shengtong") -> AnalysisInput:
    fixture = json.loads((Path(__file__).parent / f"fixtures/{fixture_name}-20260930.json").read_text())
    bars = tuple(AnalysisBar(
        date.fromisoformat(d), date.fromisoformat(d), o, h, l, c, v,
        ("tushare",), False, True, 0,
    ) for d, o, h, l, c, v in fixture["bars"] if d <= cutoff)
    return AnalysisInput(
        fixture["symbol"], AnalysisTimeframe.DAILY, AnalysisInputMode.FINAL,
        bars[-1].period_end, "raw", "shares", "observed-only", AnalysisHorizons(), (), (),
        bars, bars[-1].period_end, None, None, None, AnalysisInstrumentContext("stock"),
    )


def test_shengtong_failed_triangle_cannot_publish_old_fallback_rr():
    value = _input("2026-09-30")
    items = _generated_items(value, value.horizons, DirectionalChangeConfig())
    triangle = next(i for i in items if i.item_id == "short-pattern-symmetrical-triangle-1")
    assert triangle.payload["boundary_basis"] == "frozen-at-breakout"
    assert triangle.payload["completion_state"] == "invalidated"
    event = next(i for i in items if i.item_id == triangle.item_id + "-latest-event-evidence")
    assert event.payload["invalidation_level"] == triangle.payload["invalidation_price"]
    assert triangle.payload["invalidation_date"] == "2026-09-11"
    scenarios = [i.payload for i in items if i.item_type.value == "scenario"]
    assert all(triangle.item_id not in s["evidence_item_ids"] for s in scenarios)
    for scenario in scenarios:
        assert scenario["invalidation_evidence_item_ids"] == [scenario["stop_contract"]["setup_id"]]
        assert scenario["selected_target_label"] in {None, "T1"}
        assert all("projection-" not in t["basis"] for t in scenario["targets"])


@pytest.mark.parametrize("fixture_name", ["shengtong", "longxing"])
def test_daily_prefixes_never_use_future_target_evidence(fixture_name):
    for cutoff in (bar.period_end.isoformat() for bar in _input("2026-09-30", fixture_name).bars
                   if bar.period_end >= date(2026, 9, 1)):
        value = _input(cutoff, fixture_name)
        items = _generated_items(value, value.horizons, DirectionalChangeConfig())
        for item in items:
            if item.item_type.value != "scenario":
                continue
            p = item.payload
            assert len(p["targets"]) <= 1
            assert p["space_assessment"]["opportunity"] == p["has_trade_space"]
            assert p["as_of_date"] <= cutoff
            if p["direction"] == "long":
                assert 0 < p["invalidation_price"] < p["entry_price"]
            else:
                assert p["invalidation_price"] > p["entry_price"] > 0
            for target in p["targets"]:
                for source in target["sources"]:
                    assert source["evidence_date"] is None or source["evidence_date"] <= cutoff


def test_missing_adjustment_disables_qualification_but_keeps_reference():
    from dataclasses import replace
    value = replace(_input("2026-09-30"), warnings=(AnalysisInputWarning(
        "adjustment_factors_incomplete", "warning", "fixture raw prices",
    ),))
    scenarios = [i.payload for i in _generated_items(value, value.horizons, DirectionalChangeConfig())
                 if i.item_type.value == "scenario"]
    assert scenarios
    assert all(s["qualification_blocked"] and not s["has_trade_space"] for s in scenarios)
    assert all(s["space_assessment"]["status"] == "unavailable" for s in scenarios)
