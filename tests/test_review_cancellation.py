from datetime import date, timedelta
import threading

import pytest
from fastapi.testclient import TestClient

from stock_harness.api import create_app
from stock_harness.models import Instrument, InstrumentKind
from stock_harness.signal_review import SignalReviewService, WEEKLY_RECOGNITION_SIGNAL
from stock_harness.sqlite_store import SQLiteMarketDataStore


CUTOFF = date(2026, 9, 17)


def test_cancel_is_idempotent_and_close_drains_before_store_close(monkeypatch):
    with SQLiteMarketDataStore(":memory:") as store:
        service = SignalReviewService(store)
        entered, release = threading.Event(), threading.Event()
        visited = []

        def execute(run_id, cutoff):
            entered.set()
            assert release.wait(5)
            for i in range(10):
                service._check_stopping()
                visited.append(i)

        monkeypatch.setattr(service, "_execute", execute)
        run = service.start_run(WEEKLY_RECOGNITION_SIGNAL, CUTOFF)
        try:
            assert entered.wait(2)
            assert not service.cancel("wrong-run")
            assert service.cancel(run["run_id"])
            assert service.cancel(run["run_id"])
        finally:
            release.set()
            service.close()
        service.close()
        result = store.get_signal_review_run(run["run_id"])
        assert result["status"] == "failed" and result["phase"] == "cancelled"
        assert not visited and not service._thread.is_alive()
        assert not service.cancel(run["run_id"])


def test_cancelled_run_cannot_publish_and_next_run_can_start(monkeypatch):
    with SQLiteMarketDataStore(":memory:") as store:
        service = SignalReviewService(store)
        entered, release = threading.Event(), threading.Event()

        def execute(run_id, cutoff):
            entered.set()
            assert release.wait(5)
            service._publish(run_id, items=[], summary={}, input_digest="fixture")

        monkeypatch.setattr(service, "_execute", execute)
        first = service.start_run(WEEKLY_RECOGNITION_SIGNAL, CUTOFF)
        try:
            assert entered.wait(2)
            assert service.cancel(first["run_id"])
        finally:
            release.set()
            service._thread.join(5)
        assert store.get_signal_review_run(first["run_id"])["phase"] == "cancelled"
        assert store.list_signal_review_items(first["run_id"]) == []
        second = service.start_run(WEEKLY_RECOGNITION_SIGNAL, CUTOFF)
        service._thread.join(5)
        assert store.get_signal_review_run(second["run_id"])["status"] == "succeeded"
        service.close()


def test_attention_preview_does_not_publish_and_final_failure_rolls_it_back():
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_instruments([Instrument("600001.SH", "Fixture", InstrumentKind.STOCK, "SH")])
        update = dict(signal_id="fixture", effective_date=CUTOFF, cooldown_through=CUTOFF + timedelta(days=5),
                      promotions={"600001.SH": ["fixture-evidence"]})
        projected = store.preview_review_attention(**update)
        assert len(projected) == 1 and projected[0]["status"] == "auto-promoted"
        assert store.list_signal_attention("fixture") == []
        run = store.create_signal_review_run(signal_id="fixture", definition_version="1", algorithm_version="1",
                                            cadence="daily", effective_date=CUTOFF, parameters={})
        with pytest.raises(ValueError, match="unknown scored instruments"):
            store.complete_signal_review_run(run["run_id"], items=[], summary={}, input_digest="bad",
                                             attention_update=update, scores=[{"symbol": "UNKNOWN"}])
        assert store.list_signal_attention("fixture") == []
        store.complete_signal_review_run(run["run_id"], items=[], summary={}, input_digest="good", attention_update=update)
        actual = store.list_signal_attention("fixture")
        assert actual[0]["status"] == projected[0]["status"]
        assert actual[0]["reasons"] == projected[0]["reasons"]


def test_cancel_api_reports_accepted_unknown_and_already_finished(monkeypatch):
    with SQLiteMarketDataStore(":memory:") as store, TestClient(create_app(store)) as client:
        service = client.app.state.signal_review
        entered, release = threading.Event(), threading.Event()

        def execute(run_id, cutoff):
            entered.set()
            assert release.wait(5)
            service._check_stopping()

        monkeypatch.setattr(service, "_execute", execute)
        run = service.start_run(WEEKLY_RECOGNITION_SIGNAL, CUTOFF)
        try:
            assert entered.wait(2)
            assert client.post(f"/api/signals/runs/{run['run_id']}/cancel").status_code == 202
            assert client.post("/api/signals/runs/unknown/cancel").status_code == 404
        finally:
            release.set()
            service._thread.join(5)
        assert client.post(f"/api/signals/runs/{run['run_id']}/cancel").status_code == 409
