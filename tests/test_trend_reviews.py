from datetime import date, timedelta

from fastapi.testclient import TestClient

from stock_harness.analysis_inputs import AnalysisHorizons, AnalysisTimeframe
from stock_harness.api import create_app
from stock_harness.models import AdjustmentFactor, DailyBar, Instrument, InstrumentKind
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.trend_analysis import TrendAnalysisService
from stock_harness.trend_reviews import (
    TrendReviewDecision,
    TrendReviewDraftSpec,
    TrendReviewLabel,
    TrendReviewStatus,
    labels_from_analysis,
)


def _store() -> tuple[SQLiteMarketDataStore, list[date]]:
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("000001.SZ", "Ping An Bank", InstrumentKind.STOCK, "SZ")
    ])
    start = date(2026, 6, 1)
    days = [start + timedelta(days=index) for index in range(45)]
    closes = [10 + ((index % 10) - 5) * 0.18 for index in range(len(days))]
    store.upsert_daily_bars("tushare", [
        DailyBar(
            "000001.SZ", day, close - 0.1, close + 0.25,
            close - 0.25, close, 100 + index * 5,
        )
        for index, (day, close) in enumerate(zip(days, closes))
    ])
    store.upsert_adjustment_factors("tushare", [
        AdjustmentFactor("000001.SZ", day, 1) for day in days
    ])
    store.upsert_trading_dates("tushare", days)
    return store, days


def test_review_snapshot_is_causal_and_does_not_register_or_publish_analysis():
    store, days = _store()
    try:
        snapshot = TrendAnalysisService(store).build_review_snapshot(
            "000001.SZ",
            AnalysisTimeframe.DAILY,
            AnalysisHorizons(20, 30, 40),
            as_of_date=days[34],
            config_version="review-test-v1",
        )

        assert snapshot["as_of_date"] == days[34]
        assert snapshot["input_end_date"] == days[34]
        assert snapshot["source_observed_at_ms"] is None
        assert snapshot["items"]
        assert store.get_latest_generated_analysis_run(
            "000001.SZ", "trend", "daily"
        ) is None
        assert store.claim_generated_analysis_targets() == []
        short_labels = labels_from_analysis(snapshot["items"], "short")
        assert all(
            label.payload.get("horizon") in {None, "short"}
            for label in short_labels
        )
    finally:
        store.close()


def test_review_store_round_trip_and_optimistic_revision():
    store, days = _store()
    label = TrendReviewLabel(
        "short-support-line-0", "line", TrendReviewDecision.PENDING,
        {"first_pivot_date": days[10].isoformat()},
    )
    try:
        created = store.create_trend_review(TrendReviewDraftSpec(
            symbol="000001.SZ", timeframe="daily", horizon="short",
            interval_start=days[5], interval_end=days[30], as_of_date=days[34],
            dataset_version="2026-08-19.1", classification="positive",
            status=TrendReviewStatus.PROPOSED, tags=("trend-line",),
            labels=(label,), expected={}, rationale="initial review",
            sources=({
                "provider": "tushare", "dataset": "daily",
                "checked_on": "2026-08-19",
            },),
            input_digest=b"review-input", algorithm_version="trend-v1",
            config_version="review-v1",
            settings={
                "short_horizon_bars": 20,
                "medium_horizon_bars": 30,
                "long_horizon_bars": 40,
            },
        ))
        accepted = TrendReviewLabel(
            label.item_id, label.item_type, TrendReviewDecision.ACCEPTED,
            label.payload, "anchors are correct",
        )
        updated = store.update_trend_review(
            created["review_id"], expected_revision=1,
            status=TrendReviewStatus.PROPOSED, labels=(accepted,),
            expected={}, rationale="reviewed",
        )

        assert updated["revision"] == 2
        assert updated["labels"][0]["decision"] == "accepted"
        assert store.list_trend_reviews(symbol="000001.SZ")[0]["review_id"] == created["review_id"]
        try:
            store.update_trend_review(
                created["review_id"], expected_revision=1,
                status=TrendReviewStatus.REJECTED, labels=(accepted,),
                expected={}, rationale="stale writer",
            )
        except RuntimeError as error:
            assert "revision conflict" in str(error)
        else:
            raise AssertionError("stale review update must fail")
        confirmed = store.update_trend_review(
            created["review_id"], expected_revision=2,
            status=TrendReviewStatus.CONFIRMED, labels=(accepted,),
            expected={"trend_lines": [{"item_id": accepted.item_id}]},
            rationale="confirmed",
        )
        assert confirmed["review_status"] == "confirmed"
        try:
            store.update_trend_review(
                created["review_id"], expected_revision=3,
                status=TrendReviewStatus.REJECTED, labels=(accepted,),
                expected={}, rationale="rewrite confirmed",
            )
        except ValueError as error:
            assert "immutable" in str(error)
        else:
            raise AssertionError("confirmed review must be immutable")
    finally:
        store.close()


def test_review_api_creates_cutoff_draft_and_persists_decisions():
    store, days = _store()
    client = TestClient(create_app(store))
    payload = {
        "symbol": "000001.SZ",
        "timeframe": "daily",
        "horizon": "short",
        "interval_start": days[5].isoformat(),
        "interval_end": days[30].isoformat(),
        "as_of_date": days[34].isoformat(),
        "dataset_version": "2026-08-19.1",
        "classification": "positive",
        "tags": ["trend-line"],
        "sources": [{
            "provider": "tushare", "dataset": "daily",
            "checked_on": "2026-08-19",
        }],
        "short_horizon_bars": 20,
        "medium_horizon_bars": 60,
        "long_horizon_bars": 120,
    }
    with client:
        created_response = client.post("/api/trend-reviews", json=payload)
        assert created_response.status_code == 201
        body = created_response.json()
        review = body["review"]
        assert body["analysis"]["input_end_date"] == days[34].isoformat()
        assert review["review_status"] == "proposed"
        assert review["labels"]

        review["labels"][0]["decision"] = "accepted"
        update_payload = {
            "revision": review["revision"],
            "review_status": "proposed",
            "labels": review["labels"],
            "expected": {},
            "rationale": "first item accepted",
        }
        updated = client.put(
            f"/api/trend-reviews/{review['review_id']}", json=update_payload
        )
        stale = client.put(
            f"/api/trend-reviews/{review['review_id']}", json=update_payload
        )
        confirmed_without_labels = client.put(
            f"/api/trend-reviews/{review['review_id']}",
            json={**update_payload, "revision": 2, "review_status": "confirmed"},
        )
        listed = client.get("/api/trend-reviews", params={"symbol": "000001.SZ"})
        reopened = client.post(f"/api/trend-reviews/{review['review_id']}/snapshot")

    store.close()
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert stale.status_code == 409
    assert confirmed_without_labels.status_code == 422
    assert listed.json()["items"][0]["labels"][0]["decision"] == "accepted"
    assert reopened.status_code == 200
    assert reopened.json()["input_digest"] == review["input_digest"]
