from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from stock_harness.api import create_app
from stock_harness.models import (
    BoardMembership, CatalogEntry, DailyBar, EtfHolding, Instrument,
    InstrumentKind, MarketSnapshot,
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
        MarketSnapshot("300308.SZ", date(2026, 8, 3), 2.5, 100_000_000),
    ])
    with client:
        snapshots = client.get("/api/market-snapshots", params=[("symbol", "300308.SZ")])
        members = client.get("/api/instruments/BK1128.DC/members")
    store.close()

    assert snapshots.json()["items"][0]["total_market_cap"] == 100_000_000
    assert members.json()["relation"] == "board_constituents"
    assert members.json()["items"][0]["change_percent"] == 2.5


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
    payload = {
        "name": "Optical Leaders",
        "description": "manual",
        "members": [{"symbol": "300308.SZ", "tags": ["CPO"], "note": "core"}],
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
    assert members.json()["relation"] == "custom_group_members"
    assert members.json()["items"][0]["tags"] == ["CPO"]
    assert renamed.json()["name"] == "CPO Leaders"
    assert deleted.status_code == 204


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
    assert status.json() == {"state": "running", "rows_changed": 10}
