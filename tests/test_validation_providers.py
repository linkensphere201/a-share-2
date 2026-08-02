from datetime import date
from types import SimpleNamespace

from stock_harness.validation_providers import (
    AkShareEastmoneyValidationProvider,
    AkShareSinaValidationProvider,
    BaostockValidationProvider,
)


class _Frame:
    empty = False

    def to_dict(self, orient):
        assert orient == "records"
        return [{"日期": "1998-07-27", "开盘": 10, "最高": 11, "最低": 9, "收盘": 10.5, "成交量": 123}]


class _AkClient:
    def stock_zh_a_hist(self, **kwargs):
        assert kwargs["adjust"] == ""
        return _Frame()


class _SinaFrame:
    empty = False

    def to_dict(self, orient):
        assert orient == "records"
        return [{
            "date": "2014-06-18",
            "open": 10.88,
            "high": 11.0,
            "low": 10.48,
            "close": 10.81,
            "volume": 42000.0,
        }]


class _SinaClient:
    def stock_zh_a_daily(self, **kwargs):
        assert kwargs["symbol"] == "bj920489"
        assert kwargs["adjust"] == ""
        return _SinaFrame()


class _Result:
    error_code = "0"
    error_msg = ""
    fields = ["date", "open", "high", "low", "close", "volume", "tradestatus"]

    def __init__(self):
        self._done = False

    def next(self):
        if self._done:
            return False
        self._done = True
        return True

    def get_row_data(self):
        return ["1998-07-27", "10", "11", "9", "10.5", "12300", "1"]


class _BaoClient:
    def login(self):
        return SimpleNamespace(error_code="0", error_msg="")

    def logout(self):
        return None

    def query_history_k_data_plus(self, symbol, fields, **kwargs):
        assert symbol == "sh.600000"
        assert kwargs["adjustflag"] == "3"
        return _Result()


def test_akshare_eastmoney_maps_lots_to_shares():
    bars = AkShareEastmoneyValidationProvider(_AkClient()).fetch_symbol_daily_bars(
        "600000.SH", date(1998, 7, 27), date(1998, 7, 27)
    )
    assert bars[0].volume == 12_300


def test_baostock_maps_unadjusted_daily_bar():
    bars = BaostockValidationProvider(_BaoClient()).fetch_symbol_daily_bars(
        "600000.SH", date(1998, 7, 27), date(1998, 7, 27)
    )
    assert bars[0].volume == 12_300
    assert bars[0].close == 10.5


def test_akshare_sina_maps_bj_history_in_shares():
    bars = AkShareSinaValidationProvider(_SinaClient()).fetch_symbol_daily_bars(
        "920489.BJ", date(2014, 6, 18), date(2014, 6, 18)
    )
    assert (bars[0].high, bars[0].low, bars[0].close, bars[0].volume) == (
        11.0,
        10.48,
        10.81,
        42_000,
    )
