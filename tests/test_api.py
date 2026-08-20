from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from stock_harness.api import create_app
from stock_harness.config import FuturesExchangeCutoff
from stock_harness.models import (
    AdjustmentFactor, BoardMembership, CatalogEntry, DailyBar, EtfHolding, Instrument,
    FuturesExchange, InstrumentKind, MarketSnapshot,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore


def _client():
    store = SQLiteMarketDataStore(":memory:")
    stock = Instrument("300308.SZ", "中际旭创", InstrumentKind.STOCK, "SZ")
    board = Instrument("BK1128.DC", "CPO", InstrumentKind.SECTOR, "DC")
    store.upsert_instruments([stock])
    store.upsert_catalog_entries([
        CatalogEntry(
            board, "tushare_dc", "eastmoney", "eastmoney_board", "concept",
            board.symbol, date(2026, 8, 2), aliases=("optical module",),
        )
    ])
    store.upsert_daily_bars(
        "tushare_dc", [DailyBar(board.symbol, date(2026, 7, 31), 10, 12, 9, 11, 100)]
    )
    store.replace_board_memberships(
        "tushare_dc", board.symbol, date(2026, 8, 2),
        [BoardMembership(board.symbol, stock.symbol, stock.name, "tushare_dc", date(2026, 8, 2))],
    )
    return store, TestClient(create_app(store))


def test_search_and_instrument_detail():
    store, client = _client()
    with client:
        response = client.get("/api/instruments", params={"query": "optical"})
        detail = client.get("/api/instruments/BK1128.DC")
    store.close()

    assert response.status_code == 200
    assert response.json()["items"][0]["symbol"] == "BK1128.DC"
    assert detail.json()["rows"] == 1
    assert "optical module" in detail.json()["aliases"]
    assert detail.json()["catalog_source"] == "tushare_dc"
    assert detail.json()["open_incidents"] == []


def test_search_supports_pinyin_initials():
    store, client = _client()
    with client:
        response = client.get("/api/instruments", params={"query": "zjxc"})
    store.close()

    assert response.status_code == 200
    assert response.json()["items"][0]["symbol"] == "300308.SZ"


def test_classified_browsing_normalizes_board_sources_and_allows_empty_query():
    store, client = _client()
    observed_on = date(2026, 8, 3)
    entries = [
        CatalogEntry(
            Instrument("BK0475.DC", "半导体", InstrumentKind.SECTOR, "DC"),
            "tushare_dc", "eastmoney", "eastmoney_board", "行业板块",
            "BK0475.DC", observed_on,
        ),
        CatalogEntry(
            Instrument("885001.TI", "光模块", InstrumentKind.SECTOR, "TI"),
            "tushare_ths", "ths", "ths_board", "N", "885001.TI", observed_on,
        ),
        CatalogEntry(
            Instrument("881001.TI", "通信设备", InstrumentKind.SECTOR, "TI"),
            "tushare_ths", "ths", "ths_board", "I", "881001.TI", observed_on,
        ),
    ]
    store.upsert_catalog_entries(entries)
    store.upsert_instruments([
        Instrument("801010.SI", "农林牧渔", InstrumentKind.SECTOR, "SI"),
        Instrument("510300.SH", "沪深300ETF", InstrumentKind.ETF, "SH"),
    ])

    with client:
        concepts = client.get("/api/instruments", params={"classification": "concept", "query": ""})
        industries = client.get("/api/instruments", params={"classification": "industry", "query": ""})
        etfs = client.get("/api/instruments", params={"classification": "etf", "query": ""})
    store.close()

    assert concepts.status_code == industries.status_code == etfs.status_code == 200
    concept_rows = {item["symbol"]: item for item in concepts.json()["items"]}
    industry_rows = {item["symbol"]: item for item in industries.json()["items"]}
    assert concept_rows["BK1128.DC"]["classification_label"] == "概念板块"
    assert concept_rows["BK1128.DC"]["source_label"] == "东财"
    assert concept_rows["885001.TI"]["source_label"] == "同花顺"
    assert industry_rows["BK0475.DC"]["classification_label"] == "行业板块"
    assert industry_rows["881001.TI"]["source_label"] == "同花顺"
    assert industry_rows["801010.SI"]["source_label"] == "申万"
    assert etfs.json()["items"][0]["classification_label"] == "ETF"
    assert "has_more" in concepts.json() and "next_offset" in concepts.json()


def test_daily_bars_and_membership_directions():
    store, client = _client()
    with client:
        bars = client.get("/api/instruments/BK1128.DC/daily-bars")
        members = client.get("/api/boards/BK1128.DC/members")
        boards = client.get("/api/instruments/300308.SZ/boards")
    store.close()

    assert bars.json()["items"][0]["volume"] == 100
    assert members.json()["items"][0]["symbol"] == "300308.SZ"
    assert boards.json()["items"][0]["symbol"] == "BK1128.DC"


def test_batch_snapshots_and_generic_board_members():
    store, client = _client()
    store.upsert_market_snapshots("tushare", [
        MarketSnapshot(
            "300308.SZ", date(2026, 8, 3), 2.5, 100_000_000,
            close=52.35, volume=12_300_000, amount=645_000_000,
        ),
    ])
    with client:
        snapshots = client.get("/api/market-snapshots", params=[("symbol", "300308.SZ")])
        members = client.get("/api/instruments/BK1128.DC/members")
    store.close()

    assert snapshots.json()["items"][0]["total_market_cap"] == 100_000_000
    assert snapshots.json()["items"][0]["close"] == 52.35
    assert snapshots.json()["items"][0]["volume"] == 12_300_000
    assert snapshots.json()["items"][0]["amount"] == 645_000_000
    assert members.json()["relation"] == "board_constituents"
    assert members.json()["items"][0]["change_percent"] == 2.5
    assert members.json()["items"][0]["close"] == 52.35


def test_generic_etf_members_include_disclosure_metadata():
    store, client = _client()
    etf = Instrument("510300.SH", "CSI 300 ETF", InstrumentKind.ETF, "SH")
    store.upsert_instruments([etf])
    target = date(2026, 8, 3)
    store.replace_etf_holdings("tushare_etf_pcf", etf.symbol, target, [
        EtfHolding(etf.symbol, "300308.SZ", "Innolight", target, quantity=100, rank=1),
    ])
    with client:
        members = client.get("/api/instruments/510300.SH/members")
    store.close()

    body = members.json()
    assert body["relation"] == "etf_pcf"
    assert body["as_of_date"] == "2026-08-03"
    assert body["items"][0]["available"] is True


def test_unknown_instrument_returns_404():
    store, client = _client()
    with client:
        response = client.get("/api/instruments/UNKNOWN/daily-bars")
    store.close()

    assert response.status_code == 404


def test_custom_group_crud_search_and_member_resolution():
    store, client = _client()
    store.upsert_market_snapshots("tushare", [
        MarketSnapshot("300308.SZ", date(2026, 8, 3), 2.5, 100_000_000),
    ])
    payload = {
        "name": "Optical Leaders",
        "description": "manual",
        "members": [{
            "symbol": "300308.SZ", "role": "core_identity",
            "tags": ["CPO"], "note": "core",
        }],
    }
    with client:
        created = client.post("/api/custom-groups", json=payload)
        group = created.json()
        search = client.get("/api/instruments", params={"query": "Optical Leaders"})
        members = client.get(f"/api/instruments/CUSTOM:{group['id']}/members")
        renamed = client.put(
            f"/api/custom-groups/{group['id']}",
            json={**payload, "name": "CPO Leaders"},
        )
        deleted = client.delete(f"/api/custom-groups/{group['id']}")
    store.close()

    assert created.status_code == 201
    assert search.json()["items"][0]["kind"] == "custom-group"
    assert search.json()["items"][0]["member_count"] == 1
    assert search.json()["items"][0]["average_change_percent"] == 2.5
    assert members.json()["relation"] == "custom_group_members"
    assert members.json()["items"][0]["role"] == "core_identity"
    assert members.json()["items"][0]["tags"] == ["CPO"]
    assert renamed.json()["name"] == "CPO Leaders"
    assert deleted.status_code == 204


def test_custom_index_create_materializes_and_serves_normal_daily_bars():
    store, client = _client()
    days = [date(2026, 7, 30), date(2026, 7, 31)]
    store.upsert_trading_dates("tushare", days)
    store.upsert_daily_bars("tushare", [
        DailyBar("300308.SZ", days[0], 10, 10, 10, 10, 100),
        DailyBar("300308.SZ", days[1], 10, 12, 9, 11, 110),
    ])
    store.upsert_adjustment_factors("tushare", [
        AdjustmentFactor("300308.SZ", day, 1) for day in days
    ])
    payload = {
        "name": "Optical Index", "description": "equal weighted",
        "base_date": "2026-07-30", "base_value": 1000,
        "weighting_method": "equal",
        "members": [{"symbol": "300308.SZ"}],
    }
    with client:
        created = client.post("/api/custom-indices", json=payload)
        result = created.json()
        listed = client.get("/api/custom-indices")
        bars = client.get(f"/api/instruments/{result['symbol']}/daily-bars")
        members = client.get(f"/api/instruments/{result['symbol']}/members")
        updated = client.put(
            f"/api/custom-indices/{result['id']}",
            json={**payload, "name": "Renamed Optical Index"},
        )
        deleted = client.delete(f"/api/custom-indices/{result['id']}")
    store.close()

    assert created.status_code == 201
    assert result["status"] == "ready"
    assert result["rows"] == 2
    assert listed.json()["items"][0]["symbol"] == result["symbol"]
    assert [item["close"] for item in bars.json()["items"]] == [1000, 1100]
    assert members.json()["relation"] == "custom_index_members"
    assert members.json()["items"][0]["symbol"] == "300308.SZ"
    assert updated.json()["name"] == "Renamed Optical Index"
    assert updated.json()["revision_number"] == 2
    assert deleted.status_code == 204


def test_custom_index_definition_survives_initial_factor_failure():
    store, _ = _client()
    client = TestClient(create_app(
        store,
        custom_index_factor_loader=lambda *_args: (_ for _ in ()).throw(
            RuntimeError("provider unavailable")
        ),
    ))
    payload = {
        "name": "Retryable Index", "description": "",
        "base_date": "2026-07-31", "base_value": 1000,
        "weighting_method": "equal", "members": [{"symbol": "300308.SZ"}],
    }

    with client:
        created = client.post("/api/custom-indices", json=payload)
        listed = client.get("/api/custom-indices")
    store.close()

    assert created.status_code == 201
    assert created.json()["status"] == "error"
    assert "provider unavailable" in created.json()["last_error"]
    assert listed.json()["items"][0]["name"] == "Retryable Index"


def test_custom_group_search_supports_pinyin_initials():
    store, client = _client()
    payload = {"name": "光模块龙头", "description": "", "members": []}
    with client:
        created = client.post("/api/custom-groups", json=payload)
        search = client.get("/api/instruments", params={"query": "gmklt"})
    store.close()

    assert created.status_code == 201
    assert search.json()["items"][0]["name"] == "光模块龙头"


def test_serves_built_frontend_and_update_status(tmp_path: Path):
    web_dist = tmp_path / "web"
    web_dist.mkdir()
    (web_dist / "index.html").write_text(
        "<html><title>StockHarness</title></html>", encoding="utf-8"
    )
    store = SQLiteMarketDataStore(":memory:")
    client = TestClient(
        create_app(
            store,
            web_dist=web_dist,
            update_status=lambda: {"state": "running", "rows_changed": 10},
        )
    )
    with client:
        root = client.get("/")
        status = client.get("/api/update-status")
    store.close()

    assert root.status_code == 200
    assert "StockHarness" in root.text
    assert root.headers["cache-control"] == "no-store"
    assert status.json() == {"state": "running", "rows_changed": 10}


class _FakeIntradayService:
    def __init__(self):
        self.subscribed = []

    def status(self):
        return {"state": "ready", "enabled": True, "symbol_count": len(self.subscribed)}

    def subscribe(self, _group_id, symbols):
        self.subscribed = list(symbols)
        return self.status()

    def list(self, symbols):
        item = self.get(next(iter(symbols), ""))
        return [item] if item else []

    def refresh_symbols(self, symbols):
        self.refreshed = list(symbols)
        return self.list(symbols)

    def get(self, symbol):
        if symbol != "BK1128.DC":
            return None
        return {
            "symbol": symbol,
            "trade_date": date(2026, 8, 4),
            "open": 11,
            "high": 13,
            "low": 10,
            "close": 12,
            "volume": 200,
            "source": "test_live",
            "stale": False,
            "provider_time": "2026-08-04T14:30:00+08:00",
        }


class _FakeFuturesProvisionalService:
    def __init__(self):
        self.refreshed = []
        self.subscribed = []

    def status(self):
        return {"state": "idle", "enabled": True}

    def subscribe(self, group_id, symbols):
        self.subscribed = list(symbols)
        return {
            "state": "ready", "enabled": True,
            "group_id": group_id, "symbol_count": len(self.subscribed),
        }

    def refresh_once(self, *, manual=False, references=None):
        self.refreshed = list(references or [])
        return {
            "state": "skipped",
            "enabled": True,
            "skip_reason": "market-closed",
            "manual": manual,
        }


def test_workspace_context_is_memory_only_and_enriched_with_latest_chart_state():
    store, _ = _client()
    service = _FakeIntradayService()
    client = TestClient(create_app(store, intraday_service=service))
    payload = {
        "schema_version": "1.0",
        "published_at": "2026-08-06T12:00:00+08:00",
        "active_group_id": "group-primary",
        "active_group_name": "Default",
        "focused_window_id": "chart-1",
        "referenced_symbols": ["BK1128.DC"],
        "windows": [{
            "id": "chart-1", "type": "chart", "title": "Chart 1",
            "mode": "attached", "focused": True, "maximized": False,
            "instrument": {"symbol": "BK1128.DC", "name": "CPO", "kind": "sector"},
            "chart": {
                "range": "3Y", "coordinate_mode": "log",
                "visible_start": "2025-01-01", "visible_end": "2026-08-04",
                "volume_visible": True, "indicator": "macd",
            },
        }],
        "attachments": [],
        "drawings_by_symbol": {"BK1128.DC": [{
            "id": "line-1", "kind": "trend-line", "symbol": "BK1128.DC",
            "anchors": [
                {"date": "2026-07-01", "price": 10, "snap": "low"},
                {"date": "2026-08-01", "price": 12, "snap": "high"},
            ],
            "coordinate_mode": "normal",
            "style": {"color": "#f0b85a", "width": 2, "dash": "dashed"},
            "visible": True,
            "created_at": "2026-08-01T00:00:00Z",
            "updated_at": "2026-08-01T00:00:00Z",
        }]},
    }

    with client:
        missing = client.get("/api/workspace-context")
        accepted = client.post("/api/workspace-context", json=payload)
        context = client.get("/api/workspace-context")
    fresh_client = TestClient(create_app(store, intraday_service=service))
    with fresh_client:
        after_restart = fresh_client.get("/api/workspace-context")
    store.close()

    assert missing.status_code == 404
    assert accepted.status_code == 202
    assert context.status_code == 200
    body = context.json()
    assert body["windows"][0]["chart"]["coordinate_mode"] == "log"
    assert body["windows"][0]["latest_data_state"]["effective"]["bar_state"] == "intraday"
    assert body["windows"][0]["latest_data_state"]["latest_final"]["trade_date"] == "2026-07-31"
    assert body["drawings_by_symbol"]["BK1128.DC"][0]["anchors"][0]["snap"] == "low"
    assert after_restart.status_code == 404


def test_intraday_subscription_expands_custom_groups_and_merges_provisional_bar():
    store, _ = _client()
    group = store.create_custom_group(
        "group-live", "Live", "", [{"symbol": "300308.SZ", "tags": [], "note": ""}]
    )
    service = _FakeIntradayService()
    client = TestClient(create_app(store, intraday_service=service))
    with client:
        subscribed = client.post(
            "/api/intraday/subscription",
            json={"group_id": "workspace", "symbols": [group["symbol"], "BK1128.DC"]},
        )
        bars = client.get("/api/instruments/BK1128.DC/daily-bars")
    store.close()

    assert subscribed.status_code == 200
    assert service.subscribed == ["300308.SZ", "BK1128.DC"]
    assert [item["bar_state"] for item in bars.json()["items"]] == ["final", "intraday"]
    assert bars.json()["items"][-1]["close"] == 12


def test_intraday_subscription_partitions_mixed_group_futures_references():
    store, _ = _client()
    contract = Instrument(
        "FUT:SHFE:CU:202609", "Copper 2609",
        InstrumentKind.FUTURES_CONTRACT, "SHFE",
    )
    continuous = Instrument(
        "FUTCONT:SHFE:CU:MAIN:raw", "Copper main",
        InstrumentKind.FUTURES_CONTINUOUS, "SHFE",
    )
    store.upsert_instruments([contract, continuous])
    group = store.create_custom_group("mixed-live", "Mixed live", "", [
        {"symbol": "300308.SZ", "tags": [], "note": ""},
        {"symbol": contract.symbol, "tags": ["real"], "note": ""},
        {"symbol": continuous.symbol, "tags": ["main"], "note": ""},
    ])
    stock_service = _FakeIntradayService()
    futures_service = _FakeFuturesProvisionalService()
    client = TestClient(create_app(
        store,
        intraday_service=stock_service,
        futures_provisional_service=futures_service,
    ))
    with client:
        response = client.post("/api/intraday/subscription", json={
            "group_id": "workspace",
            "symbols": [group["symbol"], continuous.symbol, "BK1128.DC"],
        })
    store.close()

    assert response.status_code == 200
    assert stock_service.subscribed == ["300308.SZ", "BK1128.DC"]
    assert futures_service.subscribed == [contract.symbol, continuous.symbol]
    assert response.json()["futures"]["symbol_count"] == 2


def test_intraday_manual_refresh_targets_requested_chart_symbol():
    store, _ = _client()
    service = _FakeIntradayService()
    client = TestClient(create_app(store, intraday_service=service))
    with client:
        response = client.post("/api/intraday/refresh", json={"symbols": ["BK1128.DC"]})
    store.close()

    assert response.status_code == 200
    assert service.refreshed == ["BK1128.DC"]
    assert response.json()["items"][0]["source"] == "test_live"


def test_intraday_manual_refresh_routes_futures_and_returns_skip_reason():
    store = SQLiteMarketDataStore(":memory:")
    symbol = "FUTCONT:SHFE:CU:MAIN:raw"
    store.upsert_instruments([
        Instrument(
            symbol, "Copper main", InstrumentKind.FUTURES_CONTINUOUS, "SHFE"
        )
    ])
    service = _FakeFuturesProvisionalService()
    client = TestClient(create_app(store, futures_provisional_service=service))
    with client:
        response = client.post("/api/intraday/refresh", json={"symbols": [symbol]})
    store.close()

    assert response.status_code == 200
    assert service.refreshed == [symbol]
    assert response.json()["futures"]["skip_reason"] == "market-closed"
    assert response.json()["futures"]["manual"] is True


def test_futures_refresh_after_exchange_cutoff_queues_final_increment():
    store = SQLiteMarketDataStore(":memory:")
    symbol = "FUTCONT:SHFE:CU:MAIN:raw"
    store.upsert_instruments([
        Instrument(
            symbol, "Copper main", InstrumentKind.FUTURES_CONTINUOUS, "SHFE"
        )
    ])
    service = _FakeFuturesProvisionalService()
    calls = []
    china = timezone(timedelta(hours=8))
    client = TestClient(create_app(
        store,
        futures_provisional_service=service,
        futures_final_cutoffs=(
            FuturesExchangeCutoff(FuturesExchange.SHFE, time(18)),
        ),
        update_trigger=lambda: calls.append("queued") or {
            "accepted": True, "state": "queued",
        },
        now_provider=lambda: datetime(2026, 8, 21, 18, 30, tzinfo=china),
    ))
    with client:
        response = client.post("/api/intraday/refresh", json={"symbols": [symbol]})
    store.close()

    assert response.status_code == 200
    assert calls == ["queued"]
    assert response.json()["futures"]["mode"] == "final"
    assert response.json()["futures"]["state"] == "queued"


def test_futures_refresh_before_exchange_cutoff_keeps_provisional_result():
    store = SQLiteMarketDataStore(":memory:")
    symbol = "FUTCONT:SHFE:CU:MAIN:raw"
    store.upsert_instruments([
        Instrument(
            symbol, "Copper main", InstrumentKind.FUTURES_CONTINUOUS, "SHFE"
        )
    ])
    calls = []
    china = timezone(timedelta(hours=8))
    client = TestClient(create_app(
        store,
        futures_provisional_service=_FakeFuturesProvisionalService(),
        futures_final_cutoffs=(
            FuturesExchangeCutoff(FuturesExchange.SHFE, time(18)),
        ),
        update_trigger=lambda: calls.append("queued") or {
            "accepted": True, "state": "queued",
        },
        now_provider=lambda: datetime(2026, 8, 21, 17, 59, tzinfo=china),
    ))
    with client:
        response = client.post("/api/intraday/refresh", json={"symbols": [symbol]})
    store.close()

    assert response.status_code == 200
    assert calls == []
    assert response.json()["futures"]["mode"] == "provisional"
    assert response.json()["futures"]["skip_reason"] == "market-closed"


def test_manual_final_daily_update_endpoint_queues_background_refresh():
    store = SQLiteMarketDataStore(":memory:")
    calls = []
    client = TestClient(create_app(
        store,
        update_status=lambda: {"state": "idle"},
        update_trigger=lambda: calls.append("triggered") or {
            "accepted": True, "state": "queued", "trigger": "manual",
        },
    ))
    try:
        response = client.post("/api/update/refresh")
        assert response.status_code == 202
        assert response.json()["state"] == "queued"
        assert calls == ["triggered"]
    finally:
        store.close()


def test_final_daily_bar_suppresses_same_day_provisional_bar_for_all_endpoints():
    store, _ = _client()
    store.upsert_daily_bars(
        "tushare_dc",
        [DailyBar("BK1128.DC", date(2026, 8, 4), 11, 14, 10, 13, 300)],
    )
    service = _FakeIntradayService()
    client = TestClient(create_app(store, intraday_service=service))
    with client:
        polled = client.get("/api/intraday-bars", params={"symbol": "BK1128.DC"})
        refreshed = client.post(
            "/api/intraday/refresh", json={"symbols": ["BK1128.DC"]}
        )
        merged = client.get("/api/instruments/BK1128.DC/daily-bars")
    store.close()

    assert polled.json()["items"] == []
    assert refreshed.json()["items"] == []
    assert polled.json()["canonical_symbols"] == ["BK1128.DC"]
    assert refreshed.json()["canonical_symbols"] == ["BK1128.DC"]
    assert merged.json()["items"][-1]["bar_state"] == "final"
    assert merged.json()["items"][-1]["close"] == 13


def test_frontend_warning_is_available_in_runtime_event_feed():
    store, client = _client()
    with client:
        accepted = client.post("/api/runtime-events", json={
            "level": "WARNING", "logger": "test", "message": "frontend warning",
        })
        events = client.get("/api/runtime-events", params={"min_level": "WARNING"})
    store.close()

    assert accepted.status_code == 202
    assert any(item["message"] == "frontend warning" for item in events.json()["items"])


def test_trend_recalculate_endpoint_rejects_invalid_horizon_order():
    store, client = _client()
    with client:
        response = client.post("/api/analysis/trend/recalculate", json={
            "symbol": "300308.SZ",
            "timeframes": ["daily"],
            "short_horizon_bars": 120,
            "medium_horizon_bars": 60,
            "long_horizon_bars": 250,
            "config_version": "settings-1",
            "include_preview": False,
        })
    store.close()

    assert response.status_code == 422
    assert "analysis horizons" in response.json()["detail"]
