from datetime import date, timedelta

from fastapi.testclient import TestClient

from stock_harness.api import create_app
from stock_harness.major_descending_lines import MajorLinePeriod, MajorLineState
from stock_harness.models import AdjustmentFactor, DailyBar, Instrument, InstrumentKind
from stock_harness.screener import STRATEGY_VERSION, ScreenerService
from stock_harness.sqlite_store import SQLiteMarketDataStore


def _store_with_major_edge() -> tuple[SQLiteMarketDataStore, list[date]]:
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("000001.SZ", "Major Edge", InstrumentKind.STOCK, "SZ")
    ])
    start = date(2025, 1, 1)
    days = [start + timedelta(days=index) for index in range(250)]
    closes: list[float] = []
    highs: list[float] = []
    for index in range(250):
        boundary = 18 - (4.2 / 95) * (index - 20)
        closes.append(boundary - 1.5)
        highs.append(boundary - 1.2)
    closes[:20] = [16.0] * 20
    highs[:20] = [16.4] * 20
    highs[20], closes[20] = 18.0, 17.3
    highs[115], closes[115] = 13.8, 13.1
    highs[205], closes[205] = 9.82, 9.2
    latest = [8.0, 7.85, 7.95, 8.2, 7.9]
    closes[-5:] = latest
    for index, close in enumerate(latest, 245):
        highs[index] = close + 0.12
    store.upsert_daily_bars("tushare", [
        DailyBar("000001.SZ", day, close, high, close - 0.2, close, 100 + index)
        for index, (day, close, high) in enumerate(zip(days, closes, highs))
    ])
    store.upsert_adjustment_factors("tushare", [
        AdjustmentFactor("000001.SZ", day, 1) for day in days
    ])
    store.upsert_trading_dates("tushare", days)
    return store, days


def test_screener_persists_candidate_and_exact_linked_analysis():
    store, days = _store_with_major_edge()
    try:
        run = ScreenerService(store).run_sync(
            [MajorLinePeriod.YEAR], list(MajorLineState), 10, days[-1]
        )
        candidates = store.list_screener_candidates(str(run["run_id"]))

        assert run["status"] == "succeeded"
        assert run["candidate_count"] == 1
        assert candidates[0]["symbol"] == "000001.SZ"
        assert candidates[0]["evidence"]["latest_close"] == 7.9
        analysis = store.get_generated_analysis_run(candidates[0]["analysis_run_id"])
        assert analysis is not None
        assert any(
            item["item_id"] == candidates[0]["line_item_id"]
            and item["payload"]["major_line_code"] == candidates[0]["line_code"]
            for item in analysis["items"]
        )
    finally:
        store.close()


def test_screener_retains_only_ten_finished_runs():
    store, days = _store_with_major_edge()
    try:
        for _ in range(11):
            run = store.create_screener_run("strategy", "v1", days[-1], {})
            store.update_screener_progress(
                str(run["run_id"]), universe_count=0, scanned_count=0
            )
            store.complete_screener_run(str(run["run_id"]), [], retention=10)
        assert len(store.list_screener_runs(10)) == 10
    finally:
        store.close()


def test_screener_api_lists_runs_candidates_and_exact_analysis():
    store, days = _store_with_major_edge()
    run = ScreenerService(store).run_sync(
        [MajorLinePeriod.YEAR], list(MajorLineState), 10, days[-1]
    )
    candidate = store.list_screener_candidates(str(run["run_id"]))[0]
    with TestClient(create_app(store)) as client:
        runs = client.get("/api/screener/runs")
        candidates = client.get(f"/api/screener/runs/{run['run_id']}/candidates")
        analysis = client.get(f"/api/analysis/runs/{candidate['analysis_run_id']}")
    store.close()

    assert runs.status_code == 200
    assert candidates.json()["items"][0]["line_code"].startswith("MDL-1Y-")
    assert analysis.status_code == 200
    assert analysis.json()["run_id"] == candidate["analysis_run_id"]


def test_new_run_rejects_legacy_quarter_period():
    store, _ = _store_with_major_edge()
    with TestClient(create_app(store)) as client:
        response = client.post("/api/screener/runs", json={
            "periods": ["3m"],
            "states": ["critical-breakout"],
            "max_results": 10,
        })
    store.close()

    assert response.status_code == 422


def test_v1_and_v2_runs_for_same_date_remain_distinct():
    store, days = _store_with_major_edge()
    try:
        old = store.create_screener_run("major-descending-breakout", "v1", days[-1], {})
        store.update_screener_progress(
            str(old["run_id"]), universe_count=0, scanned_count=0
        )
        store.complete_screener_run(str(old["run_id"]), [])
        new = ScreenerService(store).run_sync(
            [MajorLinePeriod.YEAR], list(MajorLineState), 10, days[-1]
        )

        runs = store.list_screener_runs(10)
        assert {item["run_id"] for item in runs} >= {old["run_id"], new["run_id"]}
        assert {item["strategy_version"] for item in runs} >= {"v1", STRATEGY_VERSION}
    finally:
        store.close()


def test_completed_screener_run_can_be_deleted_without_deleting_analysis():
    store, days = _store_with_major_edge()
    try:
        run = ScreenerService(store).run_sync(
            [MajorLinePeriod.YEAR], list(MajorLineState), 10, days[-1]
        )
        candidate = store.list_screener_candidates(str(run["run_id"]))[0]

        assert store.delete_screener_run(str(run["run_id"])) is True
        assert store.get_screener_run(str(run["run_id"])) is None
        assert store.list_screener_candidates(str(run["run_id"])) == []
        assert store.get_generated_analysis_run(candidate["analysis_run_id"]) is not None
        assert store.delete_screener_run(str(run["run_id"])) is False
    finally:
        store.close()


def test_running_screener_run_cannot_be_deleted():
    store, days = _store_with_major_edge()
    try:
        run = store.create_screener_run("strategy", "v1", days[-1], {})
        try:
            store.delete_screener_run(str(run["run_id"]))
        except ValueError as error:
            assert "running" in str(error)
        else:
            raise AssertionError("running screener run must not be deleted")
    finally:
        store.close()


def test_screener_delete_api_removes_finished_run_and_reports_missing_run():
    store, days = _store_with_major_edge()
    run = ScreenerService(store).run_sync(
        [MajorLinePeriod.YEAR], list(MajorLineState), 10, days[-1]
    )
    with TestClient(create_app(store)) as client:
        deleted = client.delete(f"/api/screener/runs/{run['run_id']}")
        missing = client.delete(f"/api/screener/runs/{run['run_id']}")
    store.close()

    assert deleted.status_code == 204
    assert missing.status_code == 404
