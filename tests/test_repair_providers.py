from datetime import date
from types import SimpleNamespace

from stock_harness.repair_providers import BaostockTradeDateRepairProvider


class _Rows:
    error_code = "0"
    error_msg = ""

    def __init__(self, fields, rows):
        self.fields = fields
        self.rows = iter(rows)
        self.current = None

    def next(self):
        self.current = next(self.rows, None)
        return self.current is not None

    def get_row_data(self):
        return self.current


class _Client:
    def login(self):
        return SimpleNamespace(error_code="0", error_msg="")

    def logout(self):
        return None

    def query_all_stock(self, day):
        assert day == "2008-03-28"
        return _Rows(
            ["code", "tradeStatus", "code_name"],
            [
                ["sh.000001", "1", "index"],
                ["sh.600000", "1", "stock"],
                ["sz.000001", "1", "stock"],
                ["sz.000002", "0", "suspended"],
            ],
        )

    def query_history_k_data_plus(self, code, fields, **kwargs):
        return _Rows(
            ["date", "open", "high", "low", "close", "volume", "tradestatus"],
            [["2008-03-28", "10", "11", "9", "10.5", "12300", "1"]],
        )


def test_baostock_repair_provider_filters_to_trading_a_shares():
    batch = BaostockTradeDateRepairProvider(_Client()).fetch_stock_trade_date(
        date(2008, 3, 28)
    )
    assert [item.symbol for item in batch.instruments] == ["600000.SH", "000001.SZ"]
    assert [bar.symbol for bar in batch.bars] == ["600000.SH", "000001.SZ"]
