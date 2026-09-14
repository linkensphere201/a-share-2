from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from stock_harness.api_market_routes import create_market_router
from stock_harness.models import BoardMembership, CatalogEntry, Instrument, InstrumentKind
from stock_harness.sqlite_store import SQLiteMarketDataStore


@pytest.fixture
def store():
    value = SQLiteMarketDataStore(":memory:")
    observed = date(2026, 9, 11)
    for index in range(6):
        board = Instrument(f"BOARD{index}.DC", "PCB", InstrumentKind.SECTOR, "DC")
        value.upsert_catalog_entries([CatalogEntry(
            board, "tushare_dc", "eastmoney", "eastmoney_board", "concept", board.symbol, observed,
        )])
        value.replace_board_memberships("tushare_dc", board.symbol, observed, [
            BoardMembership(board.symbol, "000001.SZ", "Stock", "tushare_dc", observed),
        ])
    value.replace_board_memberships("tushare_dc", "BOARD5.DC", observed, [])
    yield value
    value.close()


def test_full_memberships_do_not_depend_on_selected_tags_and_preserve_board_identity(store):
    assert store.list_instrument_board_tags(["000001.SZ"]) == {"000001.SZ": []}
    values = store.list_instrument_board_memberships([" 000001.sz ", "000001.SZ", "UNKNOWN"])
    assert list(values) == ["000001.SZ", "UNKNOWN"]
    assert len(values["000001.SZ"]) == 5
    assert len({item["board_symbol"] for item in values["000001.SZ"]}) == 5
    assert {item["classification"] for item in values["000001.SZ"]} == {"concept"}
    assert values["UNKNOWN"] == []
    assert store.list_instrument_board_memberships([]) == {}


def test_membership_queries_chunk_without_dropping_results(store):
    symbols = [f"{index:06d}.SZ" for index in range(501)]
    values = store.list_instrument_board_memberships(symbols)
    assert len(values) == 501
    assert len(values["000001.SZ"]) == 5


def test_membership_endpoint_is_bounded_read_only_and_labels_current_basis(store, monkeypatch):
    monkeypatch.setattr("stock_harness.api_market_routes.store", lambda request: store)
    app = FastAPI()
    app.include_router(create_market_router())
    with TestClient(app) as client:
        response = client.get("/api/instrument-board-memberships", params=[("symbol", " 000001.sz ")])
        assert response.status_code == 200
        assert response.json()["basis"] == "current"
        assert len(response.json()["items"][0]["boards"]) == 5
        assert client.get("/api/instrument-board-memberships").json()["items"] == []
        assert client.get("/api/instrument-board-memberships", params=[("symbol", "X")] * 501).status_code == 422
