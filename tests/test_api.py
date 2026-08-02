from datetime import date

from fastapi.testclient import TestClient

from stock_harness.api import create_app
from stock_harness.models import BoardMembership, CatalogEntry, DailyBar, Instrument, InstrumentKind
from stock_harness.sqlite_store import SQLiteMarketDataStore


def _client():
    store = SQLiteMarketDataStore(":memory:")
    stock = Instrument("300308.SZ", "Innolight", InstrumentKind.STOCK, "SZ")
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


def test_unknown_instrument_returns_404():
    store, client = _client()
    with client:
        response = client.get("/api/instruments/UNKNOWN/daily-bars")
    store.close()

    assert response.status_code == 404
