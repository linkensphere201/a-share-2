from datetime import date

from fastapi.testclient import TestClient

from stock_harness.api import create_app
from stock_harness.models import ActiveMarketValueFeature, DailyBar, Instrument, InstrumentKind
from stock_harness.sqlite_store import SQLiteMarketDataStore


def test_daily_turnover_matches_symbol_and_date_without_filling_gaps():
    store = SQLiteMarketDataStore(":memory:")
    stock = "000001.SZ"
    other = "000002.SZ"
    board = "BK1128.DC"
    dates = [date(2026, 9, day) for day in (14, 15, 16)]
    store.upsert_instruments([
        Instrument(stock, "Stock", InstrumentKind.STOCK, "SZ"),
        Instrument(other, "Other", InstrumentKind.STOCK, "SZ"),
        Instrument(board, "Board", InstrumentKind.SECTOR, "DC"),
    ])
    store.upsert_daily_bars("tushare", [
        DailyBar(symbol, day, 10, 12, 9, 11, 100)
        for symbol in (stock, other, board) for day in dates
    ])
    for day, rate in ((dates[0], 2.35), (dates[2], 0.0)):
        store.upsert_active_market_value_features("tushare", day, [
            ActiveMarketValueFeature(stock, day, rate, 1000, 10000, 20000, 11),
            ActiveMarketValueFeature(other, day, 99, 1000, 10000, 20000, 11),
        ])
    assert store.get_daily_turnover_rates(stock.lower(), dates[1], dates[2]) == {dates[2]: 0}
    with TestClient(create_app(store)) as client:
        rows = client.get(f"/api/instruments/{stock}/daily-bars").json()["items"]
        assert [row["turnover_rate_f"] for row in rows] == [2.35, None, 0]
        bounded = client.get(f"/api/instruments/{stock}/daily-bars", params={
            "start_date": dates[1].isoformat(), "end_date": dates[1].isoformat(),
        }).json()["items"]
        assert len(bounded) == 1 and bounded[0]["turnover_rate_f"] is None
        assert client.get(f"/api/instruments/{stock}/daily-bars", params={
            "end_date": "2020-01-01",
        }).json()["items"] == []
        board_rows = client.get(f"/api/instruments/{board}/daily-bars").json()["items"]
        assert all("turnover_rate_f" not in row for row in board_rows)
    store.close()
