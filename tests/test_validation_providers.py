from datetime import date
from types import SimpleNamespace

from stock_harness.validation_providers import (
    AkShareEastmoneyValidationProvider,
    AkShareEastmoneyBoardValidationProvider,
    AkShareSinaValidationProvider,
    AkShareSwValidationProvider,
    AkShareThsBoardValidationProvider,
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


class _SwFrame:
    empty = False

    def itertuples(self, index=False, name=None):
        assert index is False
        assert name is None
        return iter([
            ("801080", date(2015, 12, 30), 3582.11, 3521.64, 3582.2, 3515.6, 25.47878634, 500.0)
        ])


class _SwClient:
    def index_hist_sw(self, **kwargs):
        assert kwargs == {"symbol": "801080", "period": "day"}
        return _SwFrame()


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


def test_akshare_sw_maps_official_sector_bar_and_volume_to_shares():
    bars = AkShareSwValidationProvider(_SwClient()).fetch_symbol_daily_bars(
        "801080.SI", date(2015, 12, 30), date(2015, 12, 30)
    )
    assert (bars[0].high, bars[0].close, bars[0].volume) == (
        3582.2,
        3582.11,
        2_547_878_634,
    )


class _BoardFrame:
    empty = False

    def __init__(self, rows):
        self.rows = rows

    def itertuples(self, index=False, name=None):
        assert index is False and name is None
        return iter(self.rows)


class _BoardClient:
    def stock_board_concept_hist_em(self, **_kwargs):
        return _BoardFrame([("2026-07-31", 10, 11, 12, 9, 0, 0, 1234)])

    def stock_board_concept_index_ths(self, **_kwargs):
        return _BoardFrame([("2026-07-31", 20, 22, 19, 21, 5678, 0)])


def test_akshare_eastmoney_board_adapter_preserves_dc_symbol():
    bars = AkShareEastmoneyBoardValidationProvider(
        {"BK1128.DC": ("CPO", "\u6982\u5ff5\u677f\u5757")}, _BoardClient()
    ).fetch_symbol_daily_bars("BK1128.DC", date(2026, 7, 31), date(2026, 7, 31))

    assert (bars[0].symbol, bars[0].open, bars[0].high, bars[0].volume) == (
        "BK1128.DC", 10, 12, 1234,
    )


def test_akshare_ths_board_adapter_preserves_ti_symbol():
    bars = AkShareThsBoardValidationProvider(
        {"886033.TI": ("CPO", "N")}, _BoardClient()
    ).fetch_symbol_daily_bars("886033.TI", date(2026, 7, 31), date(2026, 7, 31))

    assert (bars[0].symbol, bars[0].close, bars[0].volume) == (
        "886033.TI", 21, 5678,
    )
