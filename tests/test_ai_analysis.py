from datetime import date

from fastapi.testclient import TestClient

from stock_harness.analysis_results import (
    AnalysisNamespace, AnalysisRunSpec, GeneratedAnalysisItem, GeneratedItemType,
)
from stock_harness.api import create_app
from stock_harness.models import Instrument, InstrumentKind
from stock_harness.sqlite_store import SQLiteMarketDataStore


def _payload() -> dict[str, object]:
    return {
        "symbol": "000001.SZ",
        "timeframe": "daily",
        "as_of_date": "2026-08-31",
        "title": "AI trend review",
        "conclusion_markdown": "[K1] is support. [L1] is the active resistance line.",
        "framework": {
            "key_level_codes": ["K1"],
            "structures": [
                {
                    "horizon": "small", "trend": "recovering", "pattern": "base",
                    "state": "forming", "reference_codes": ["K1"],
                },
                {
                    "horizon": "medium", "trend": "down", "pattern": "channel",
                    "state": "unbroken", "reference_codes": ["L1"],
                },
            ],
            "risk_reward": [{
                "name": "support confirmation", "direction": "long",
                "trigger": "hold K1", "entry_price": 10, "stop_price": 9,
                "target_price": 12, "reference_codes": ["K1", "L1"],
            }],
        },
        "references": [
            {
                "code": "K1", "kind": "level", "label": "support", "detail": "recent low",
                "geometry": {"lower": 9.9, "upper": 10.1},
            },
            {
                "code": "L1", "kind": "line", "label": "resistance", "detail": "two highs",
                "geometry": {
                    "start_date": "2026-08-01", "start_price": 13,
                    "end_date": "2026-08-20", "end_price": 12,
                    "role": "resistance", "horizon": "medium",
                },
            },
        ],
        "author": "codex",
    }


def test_ai_analysis_appends_versions_and_computes_risk_reward():
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("000001.SZ", "Ping An Bank", InstrumentKind.STOCK, "SZ")
    ])
    with TestClient(create_app(store)) as client:
        first = client.post("/api/analysis/ai", json=_payload())
        second = client.post("/api/analysis/ai", json=_payload())
        latest = client.get("/api/analysis/ai/000001.SZ")
    store.close()

    assert first.status_code == 201
    assert first.json()["revision"] == 1
    assert first.json()["framework"]["risk_reward"][0]["risk_reward_ratio"] == 2
    assert second.json()["revision"] == 2
    assert latest.json()["report_id"] == second.json()["report_id"]
    assert latest.json()["references"][1]["geometry"]["horizon"] == "medium"


def test_ai_analysis_requires_both_horizons_and_explicit_code_citations():
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("000001.SZ", "Ping An Bank", InstrumentKind.STOCK, "SZ")
    ])
    missing_horizon = _payload()
    missing_horizon["framework"]["structures"][1]["horizon"] = "small"  # type: ignore[index]
    missing_citation = _payload()
    missing_citation["conclusion_markdown"] = "Only [K1] is cited."
    with TestClient(create_app(store)) as client:
        horizon_response = client.post("/api/analysis/ai", json=missing_horizon)
        citation_response = client.post("/api/analysis/ai", json=missing_citation)
    store.close()

    assert horizon_response.status_code == 422
    assert "small (7-14) and medium (14-28)" in horizon_response.json()["detail"]
    assert citation_response.status_code == 422
    assert "L1" in citation_response.json()["detail"]


def test_ai_analysis_can_bind_generated_items_and_protects_the_source_run():
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("000001.SZ", "Ping An Bank", InstrumentKind.STOCK, "SZ")
    ])
    started = store.begin_generated_analysis_run(AnalysisRunSpec(
        system_id="trend", symbol="000001.SZ", timeframe="daily",
        namespace=AnalysisNamespace.PREVIEW, as_of_date=date(2026, 8, 31),
        input_start_date=date(2026, 1, 1), input_end_date=date(2026, 8, 31),
        input_digest=b"ai-source", algorithm_version="trend-test",
        config_version="test", completion_state="partial",
        source_observed_at_ms=1, expires_at_ms=1,
    ))
    store.complete_generated_analysis_run(started.run_id, [
        GeneratedAnalysisItem(
            "key-level-0", GeneratedItemType.ZONE,
            {"kind": "key-level", "lower": 9.9, "upper": 10.1},
        )
    ], duration_ms=1)
    payload = _payload()
    payload["source_run_id"] = started.run_id
    payload["references"][0]["analysis_item_id"] = "key-level-0"  # type: ignore[index]
    payload["references"][0]["geometry"] = None  # type: ignore[index]
    with TestClient(create_app(store)) as client:
        response = client.post("/api/analysis/ai", json=payload)
    removed = store.prune_generated_analysis_runs(
        2, preview_retention_ms=0, failed_retention_ms=0
    )
    retained = store.get_latest_generated_analysis_run(
        "000001.SZ", "trend", "daily", AnalysisNamespace.PREVIEW
    )
    store.close()

    assert response.status_code == 201
    assert response.json()["references"][0]["analysis_item_id"] == "key-level-0"
    assert response.json()["references"][0]["snapshot"]["item_type"] == "zone"
    assert removed == 0
    assert retained is not None
