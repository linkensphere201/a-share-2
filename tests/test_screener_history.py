from datetime import date

import pytest
from fastapi.testclient import TestClient

from stock_harness.api import create_app
from stock_harness.sqlite_store import SQLiteMarketDataStore


def create(store, count=8):
    runs = [store.create_screener_run("major-descending-breakout", "fixture", date(2026, 9, 17), {})
            for _ in range(count)]
    # Force timestamp ties to exercise the second cursor component.
    with store._lock, store._transaction():
        store._connection.execute("UPDATE screener_runs SET started_at_ms = 1000")
    return runs


def test_history_cursor_survives_completion_insertion_and_deleted_anchor():
    with SQLiteMarketDataStore(":memory:") as store:
        create(store)
        expected = store.list_screener_history(100)["items"]
        first = store.list_screener_history(3)
        anchor = first["items"][-1]["run_id"]
        store.complete_screener_run(anchor, [])
        store.delete_screener_run(anchor)
        store.complete_screener_run(expected[4]["run_id"], [])
        store.create_screener_run("major-descending-breakout", "new", date(2026, 9, 17), {})
        second = store.list_screener_history(3, first["next_cursor"])
        third = store.list_screener_history(3, second["next_cursor"])
        assert [item["run_id"] for item in second["items"] + third["items"]] == [
            item["run_id"] for item in expected[3:]]
        assert not third["has_more"] and third["next_cursor"] is None
        plan = store._connection.execute("EXPLAIN QUERY PLAN SELECT run_id FROM screener_runs "
            "WHERE (started_at_ms, run_id) < (?, ?) ORDER BY started_at_ms DESC, run_id DESC LIMIT 51",
            (1000, anchor)).fetchall()
        assert any("screener_runs_history_cursor" in str(tuple(row)) for row in plan)


def test_activity_is_bounded_by_active_and_known_runs_not_history():
    with SQLiteMarketDataStore(":memory:") as store:
        runs = create(store, 120)
        for run in runs[:-2]:
            store.complete_screener_run(run["run_id"], [])
        assert len(store.list_screener_activity()) == 2
        items = store.list_screener_activity([runs[0]["run_id"]])
        assert len(items) == 3
        assert any(item["status"] == "succeeded" for item in items)
        with pytest.raises(ValueError):
            store.list_screener_activity(["x"] * 101)


def test_history_api_cursor_validation_and_legacy_compatibility():
    with SQLiteMarketDataStore(":memory:") as store, TestClient(create_app(store)) as client:
        runs = create(store)
        first = client.get("/api/screener/history", params={"limit": 5}).json()
        assert len(first["items"]) == 5 and first["has_more"]
        second = client.get("/api/screener/history", params={"limit": 5, "cursor": first["next_cursor"]}).json()
        assert len(second["items"]) == 3 and not second["has_more"]
        for invalid in ["", "-1:id", "bad", "1:"]:
            assert client.get("/api/screener/history", params={"cursor": invalid}).status_code == 422
        assert len(client.get("/api/screener/runs", params={"limit": 3, "offset": 2}).json()["items"]) == 3
        store.complete_screener_run(runs[0]["run_id"], [])
        assert len(client.get("/api/screener/activity").json()["items"]) == 7
        assert len(client.get("/api/screener/activity", params={"run_id": runs[0]["run_id"]}).json()["items"]) == 8
