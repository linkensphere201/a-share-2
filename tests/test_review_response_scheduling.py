import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Event

import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from stock_harness.api import create_app
from stock_harness.models import DailyBar, Instrument, InstrumentKind
from stock_harness.sqlite_store import SQLiteMarketDataStore


@pytest.mark.parametrize("endpoint", ["items", "items?summary=true", "board-observations"])
def test_large_review_response_is_encoded_off_event_loop(endpoint, monkeypatch):
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_instruments([Instrument("000001.SZ", "test", InstrumentKind.STOCK, "SZ")])
        store.upsert_daily_bars("tushare", [DailyBar("000001.SZ", date(2026, 9, 17), 10, 11, 9, 10, 100)])
        monkeypatch.setattr(store, "get_signal_review_run", lambda _: {"run_id": "review"})
        payload = {"symbol": "000001.SZ", "payload": {"evidence": [{"value": i} for i in range(100)]}}
        if endpoint.startswith("items"):
            monkeypatch.setattr(store, "list_signal_review_items", lambda *args, **kwargs: [payload])
            monkeypatch.setattr(store, "count_signal_review_items", lambda *args: 1)
        else:
            payload = {**payload, "effective_date": date(2026, 9, 17)}
            monkeypatch.setattr(store, "list_board_daily_observations", lambda **kwargs: [payload])
            monkeypatch.setattr(store, "count_board_daily_observations", lambda *args: 1)
        render = JSONResponse.render
        checked = []
        encoding_started = Event()
        release_encoding = Event()

        def checked_render(self, content):
            if isinstance(content, dict) and content.get("total") == 1:
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    checked.append(True)
                else:
                    pytest.fail("Review JSON encoding blocks the API event loop")
                encoding_started.set()
                assert release_encoding.wait(5), "Concurrent daily request did not finish"
            return render(self, content)

        monkeypatch.setattr(JSONResponse, "render", checked_render)
        with TestClient(create_app(store=store)) as client:
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(client.get, f"/api/signals/runs/review/{endpoint}")
                try:
                    assert encoding_started.wait(3)
                    daily = client.get("/api/instruments/000001.SZ/daily-bars")
                    assert daily.status_code == 200
                    assert daily.json()["items"][0]["close"] == 10
                    assert not pending.done()
                finally:
                    release_encoding.set()
                response = pending.result(timeout=5)
            assert response.status_code == 200
            assert checked == [True]
            item = response.json()["items"][0]
            assert item["payload"] == payload["payload"]
            if endpoint == "board-observations":
                assert item["effective_date"] == "2026-09-17"
