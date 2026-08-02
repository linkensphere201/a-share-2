from __future__ import annotations

import unittest
from collections import namedtuple
from datetime import date

from stock_harness.config import TushareSettings
from stock_harness.tushare_provider import TushareDailyProvider


class _Frame:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def itertuples(self, index: bool = False):
        del index
        return iter(self.rows)


class _Client:
    def stock_basic(self, **kwargs):
        Row = namedtuple("Row", "ts_code name market list_date delist_date")
        status = kwargs["list_status"]
        rows = {
            "L": [Row("600519.SH", "Moutai", "Main", "20010827", None)],
            "D": [Row("600001.SH", "Delisted", "Main", "19900101", "20000101")],
            "P": [],
        }[status]
        return _Frame(rows)

    def trade_cal(self, **_kwargs):
        Row = namedtuple("Row", "cal_date is_open")
        return _Frame([Row("20260731", 1), Row("20260803", 1)])

    def daily(self, **kwargs):
        Row = namedtuple("Row", "ts_code trade_date open high low close vol")
        return _Frame([Row("600519.SH", kwargs["trade_date"], 10.0, 12.0, 9.0, 11.0, 123.0)])


class _JsonClient:
    def stock_basic(self, **kwargs):
        if kwargs["list_status"] != "L":
            return []
        return [{"ts_code": "600519.SH", "name": "Moutai"}]

    def trade_cal(self, **_kwargs):
        return [{"cal_date": "20260731", "is_open": 1}]

    def daily(self, **kwargs):
        return [{
            "ts_code": "600519.SH", "trade_date": kwargs["trade_date"],
            "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "vol": 123.0,
        }]


def _settings() -> TushareSettings:
    return TushareSettings("TUSHARE_TOKEN", None, 0, 0, 0, 1)


class TushareDailyProviderTests(unittest.TestCase):
    def test_lists_active_and_historical_instruments(self) -> None:
        instruments = TushareDailyProvider(_settings(), client=_Client()).list_instruments()

        self.assertEqual([item.symbol for item in instruments], ["600001.SH", "600519.SH"])
        self.assertEqual([item.active for item in instruments], [False, True])

    def test_maps_daily_volume_from_lots_to_shares(self) -> None:
        provider = TushareDailyProvider(_settings(), client=_Client())

        bar = provider.fetch_daily_bars(date(2026, 7, 31))[0]

        self.assertEqual(bar.trade_date, date(2026, 7, 31))
        self.assertEqual(bar.volume, 12_300)

    def test_accepts_lightweight_json_rows_without_dataframes(self) -> None:
        provider = TushareDailyProvider(_settings(), client=_JsonClient())

        self.assertEqual(len(provider.list_instruments()), 1)
        self.assertEqual(provider.trading_dates(date(2026, 7, 1), date(2026, 7, 31)), [date(2026, 7, 31)])
        self.assertEqual(provider.fetch_daily_bars(date(2026, 7, 31))[0].volume, 12_300)


if __name__ == "__main__":
    unittest.main()
