from datetime import date, timedelta
import time
import sqlite3
import json
from pathlib import Path

from fastapi.testclient import TestClient

from stock_harness.api import create_app
from stock_harness.major_descending_lines import MajorLinePeriod, MajorLineState
from stock_harness.models import AdjustmentFactor, DailyBar, Instrument, InstrumentKind
from stock_harness.screener import STRATEGY_VERSION, ScreenerService
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.sqlite_schema import SCHEMA
from stock_harness.pattern_analysis import PatternAnalysisService


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


def test_latest_screening_date_uses_bounded_index_probes_and_only_active_stocks():
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_instruments([
            Instrument("000001.SZ", "Active", InstrumentKind.STOCK, "SZ"),
            Instrument("000002.SZ", "Inactive", InstrumentKind.STOCK, "SZ", active=False),
            Instrument("510300.SH", "ETF", InstrumentKind.ETF, "SH"),
            Instrument("000003.SZ", "No bars", InstrumentKind.STOCK, "SZ"),
        ])
        assert store.get_latest_stock_daily_bar_date() is None
        start = date(2000, 1, 1)
        days = [start + timedelta(days=i) for i in range(6000)]
        store.upsert_daily_bars("test", [
            DailyBar("000001.SZ", day, 10, 11, 9, 10, 100) for day in days
        ] + [DailyBar(symbol, date(2026, 9, 15), 10, 11, 9, 10, 100)
             for symbol in ("000002.SZ", "510300.SH")])
        instructions = 0

        def bounded_work() -> int:
            nonlocal instructions
            instructions += 100
            return int(instructions > 1000)

        # Abort a full-history scan instead of relying on machine-sensitive timings.
        store._connection.set_progress_handler(bounded_work, 100)
        try:
            assert store.get_latest_stock_daily_bar_date() == days[-1]
        finally:
            store._connection.set_progress_handler(None, 0)


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
        scenario = next(
            item for item in analysis["items"]
            if item["item_id"] == candidates[0]["evidence"]["scenario_item_id"]
        )
        assert scenario["item_type"] == "scenario"
        assert scenario["payload"]["contract_version"] == "structural-trade-scenario-v3-current-entry"
        assert candidates[0]["evidence"]["trade_scenario"] == scenario["payload"]
        assert candidates[0]["evidence"]["invalidation_price"] == scenario["payload"]["invalidation_price"]
    finally:
        store.close()


def test_major_screener_uses_one_authoritative_result_per_symbol(monkeypatch):
    store, days = _store_with_major_edge()
    calls = []
    original = PatternAnalysisService.analyze

    def analyze(service, request):
        calls.append(request)
        return original(service, request)

    monkeypatch.setattr(PatternAnalysisService, "analyze", analyze)
    try:
        run = ScreenerService(store).run_sync(
            [MajorLinePeriod.YEAR], list(MajorLineState), 10, days[-1],
        )
        assert len(calls) == 1
        candidate = store.list_screener_candidates(str(run["run_id"]))[0]
        analysis = store.get_generated_analysis_run(candidate["analysis_run_id"])
        line = next(item for item in analysis["items"] if item["item_id"] == candidate["line_item_id"])
        assert candidate["state"] == line["payload"]["major_line_state"]
        assert candidate["score"] == line["payload"]["score"]
        assert candidate["line_code"] == line["payload"]["major_line_code"]
    finally:
        store.close()


def test_major_screener_respects_full_analysis_for_reported_300102_failure(monkeypatch):
    fixture = json.loads((Path(__file__).parent / "fixtures" /
                          "major_descending_300102_20260914.json").read_text(encoding="utf-8"))
    symbol = fixture["symbol"]
    cutoff = date.fromisoformat(fixture["as_of_date"])
    calls = []
    original = PatternAnalysisService.analyze

    def analyze(service, request):
        result = original(service, request)
        calls.append(result[0])
        return result

    monkeypatch.setattr(PatternAnalysisService, "analyze", analyze)
    with SQLiteMarketDataStore(":memory:") as store:
        store.upsert_instruments([Instrument(symbol, "Regression 300102", InstrumentKind.STOCK, "SZ")])
        days = [date.fromisoformat(row[0]) for row in fixture["bars"]]
        store.upsert_daily_bars("tushare", [
            DailyBar(symbol, day, *row[1:]) for day, row in zip(days, fixture["bars"])
        ])
        store.upsert_adjustment_factors("tushare", [AdjustmentFactor(symbol, day, 1) for day in days])
        store.upsert_trading_dates("tushare", days)
        run = ScreenerService(store).run_sync(
            [MajorLinePeriod.HALF_YEAR, MajorLinePeriod.YEAR], list(MajorLineState), 10, cutoff,
        )
        assert run["status"] == "succeeded"
        assert len(calls) == 1
        lines = [item for item in calls[0]["items"] if item["item_type"] == "line"]
        assert any(item["item_id"] == "major-descending-6m-20260126-20260818"
                   and item["payload"]["major_line_state"] == "forming" for item in lines)
        assert not any(item["item_id"] == "major-descending-6m-20260204-20260818" for item in lines)
        assert store.list_screener_candidates(str(run["run_id"])) == []


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


def test_screener_lists_and_accepts_volume_accumulation_strategy():
    store, _ = _store_with_major_edge()
    with TestClient(create_app(store)) as client:
        strategies = client.get("/api/screener/strategies")
        response = client.post("/api/screener/runs", json={
            "strategy_id": "volume-accumulation-20d",
            "max_results": 10,
        })
        run_id = response.json()["run_id"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            current = client.get(f"/api/screener/runs/{run_id}").json()
            if current["status"] != "running":
                break
            time.sleep(0.01)
    store.close()

    assert strategies.status_code == 200
    assert {item["strategy_id"] for item in strategies.json()["items"]} >= {
        "major-descending-breakout", "volume-accumulation-20d",
    }
    assert response.status_code == 202
    assert response.json()["strategy_id"] == "volume-accumulation-20d"


def test_existing_screener_schema_is_extended_without_losing_runs(tmp_path: Path):
    database = tmp_path / "legacy.sqlite"
    legacy_schema = SCHEMA.replace(
        "'critical-breakout', 'breakout-retest', 'broken-out', 'accumulating'",
        "'critical-breakout', 'breakout-retest', 'broken-out'",
    )
    connection = sqlite3.connect(database)
    connection.executescript(legacy_schema)
    connection.close()

    with SQLiteMarketDataStore(database) as store:
        sql = store._connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'screener_candidates'"
        ).fetchone()[0]

    assert "'accumulating'" in sql


def test_volume_accumulation_run_persists_independent_candidate():
    store, days = _store_with_major_edge()
    try:
        replacement = []
        closes = (
            [16.0 - index * .09 for index in range(60)]
            + [10.6, 10.55, 10.5, 10.48, 10.5, 11.05, 11.1, 11.12, 11.15, 11.18]
            + [11.2, 11.3, 11.15, 11.28, 11.18, 11.32, 11.22, 11.35, 11.25, 11.38]
        )
        volumes = [100] * 70 + [150, 105, 155, 100, 160, 105, 165, 100, 170, 105]
        for index, (trade_date, close, volume) in enumerate(zip(days[-80:], closes, volumes)):
            open_price = close * (0.995 if index % 2 == 0 else 1.005)
            replacement.append(DailyBar(
                "000001.SZ", trade_date, open_price, close * 1.01,
                close * .99, close, volume,
            ))
        store.upsert_daily_bars("tushare", replacement)

        run = ScreenerService(store).run_sync(
            [MajorLinePeriod.YEAR], list(MajorLineState), 10, days[-1],
            "volume-accumulation-20d",
        )
        candidates = store.list_screener_candidates(str(run["run_id"]))

        assert run["strategy_id"] == "volume-accumulation-20d"
        assert run["strategy_version"] == "volume-accumulation-20d-v6"
        assert candidates[0]["state"] == "accumulating"
        assert candidates[0]["line_code"] == "VOL-ACC-20D"
        assert candidates[0]["evidence"]["algorithm_version"] == "decline-platform-accumulation-v3"
        linked = store.get_generated_analysis_run(candidates[0]["analysis_run_id"])
        assert linked is not None
        assert any(
            item["item_type"] == "zone"
            and item["payload"].get("kind") == "accumulation-range"
            for item in linked["items"]
        )
        zone = next(item for item in linked["items"]
                    if item["payload"].get("kind") == "accumulation-range")
        evidence = candidates[0]["evidence"]
        assert candidates[0]["line_item_id"] == zone["item_id"]
        assert candidates[0]["score"] == zone["payload"]["score"]
        for field in ("stage", "score_components", "platform_sessions", "limit_up_events"):
            assert evidence[field] == zone["payload"][field]
        assert evidence["range_lower"] == zone["payload"]["lower"]
        assert evidence["range_upper"] == zone["payload"]["upper"]
    finally:
        store.close()


def test_secondary_base_screener_and_linked_chart_publish_identical_evidence():
    from test_volume_accumulation import _secondary_bars
    store, days = _store_with_major_edge()
    try:
        bars = _secondary_bars()
        store.upsert_daily_bars("tushare", [
            DailyBar("000001.SZ", day, b.open, b.high, b.low, b.close, b.volume)
            for day, b in zip(days[-len(bars):], bars)
        ])
        run = ScreenerService(store).run_sync(
            [MajorLinePeriod.YEAR], list(MajorLineState), 10, days[-1],
            "volume-accumulation-20d",
        )
        candidates = store.list_screener_candidates(str(run["run_id"]))
        assert len(candidates) == 1
        candidate = candidates[0]
        linked = store.get_generated_analysis_run(candidate["analysis_run_id"])
        assert linked is not None
        zone = next(x for x in linked["items"] if x["item_id"] == candidate["line_item_id"])
        assert candidate["evidence"]["pattern_type"] == "secondary-base"
        for field in ("pattern_type", "stage", "demand_regime", "first_bottom_date",
                      "second_bottom_date", "pullback_volume_ratio", "score_components"):
            assert candidate["evidence"][field] == zone["payload"][field]
        assert candidate["score"] == zone["payload"]["score"]
        assert candidate["evidence"]["range_lower"] == zone["payload"]["lower"]
        assert candidate["evidence"]["range_upper"] == zone["payload"]["upper"]
    finally:
        store.close()


def test_latest_succeeded_run_can_be_selected_by_strategy():
    store, days = _store_with_major_edge()
    try:
        major = store.create_screener_run(
            "major-descending-breakout", "major-v1", days[-1], {},
        )
        store.complete_screener_run(str(major["run_id"]), [])
        accumulation = store.create_screener_run(
            "volume-accumulation-20d", "volume-v1", days[-1], {},
        )
        store.complete_screener_run(str(accumulation["run_id"]), [])

        selected = store.get_latest_succeeded_screener_run(
            days[-1], "major-descending-breakout",
        )

        assert selected is not None
        assert selected["run_id"] == major["run_id"]
    finally:
        store.close()


def test_large_drop_without_required_structure_is_not_an_accumulation_candidate():
    store, days = _store_with_major_edge()
    try:
        closes = [10.0] * 20
        closes[10] = 9.2
        closes[11:] = [9.9] * 9
        replacement = [
            DailyBar(
                "000001.SZ", trade_date, close, close + 0.1,
                close - 0.1, close, 150,
            )
            for trade_date, close in zip(days[-20:], closes)
        ]
        store.upsert_daily_bars("tushare", replacement)

        run = ScreenerService(store).run_sync(
            [MajorLinePeriod.YEAR], list(MajorLineState), 10, days[-1],
            "volume-accumulation-20d",
        )
        assert run["candidate_count"] == 0
    finally:
        store.close()


def test_screener_exclusion_pool_api_is_removed():
    store, days = _store_with_major_edge()
    with TestClient(create_app(store)) as client:
        response = client.get("/api/screener/exclusion-pool")
    store.close()

    assert response.status_code == 404


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
