from datetime import date

from stock_harness.replay.signals import signal_from_analysis_result
from stock_harness.review_systems.mean_reversion import _chart_projection, _conclusion_sections
from stock_harness.review_systems.scorer_adapter import _complete_result


def test_mean_chart_uses_same_corrected_boundaries_as_conclusion():
    facts = {"confirmation": {"boundary_price": 10}, "invalidation_price": 8}
    opportunity = {"confirmation_price": 10.5, "invalidation_price": 9}
    projections = _chart_projection(facts, [], opportunity)
    assert next(p for p in projections if p["role"] == "confirmation")["price"] == 10.5
    assert next(p for p in projections if p["role"] == "invalidation")["price"] == 9
    sections = _conclusion_sections({"parent_trend": "up", "price_volume": {"path": "volume-backed-reclaim"}}, opportunity)
    assert "上涨" in sections[0]["text"]
    assert "放量收复确认位" in sections[2]["text"]
    assert "mr:deviation-band" in sections[1]["evidence_refs"]


def test_trend_contract_preserves_waiting_stage_prices_and_score_for_replay():
    result = _complete_result({
        "symbol": "TEST", "system_id": "trend", "scorer_version": "v1",
        "entity_scope": "board", "eligible": True, "setup_state": "waiting-trigger",
        "total_score": 78, "entry_price": 10, "invalidation_price": 9,
        "selected_target_price": 12,
    }, "trend")
    frozen = signal_from_analysis_result(result, date(2026, 1, 1), reference_close=10)
    assert frozen.score == 78
    assert frozen.metadata["state"] == "waiting-trigger"
    assert frozen.invalidation_price == 9
