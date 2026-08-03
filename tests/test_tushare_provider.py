from __future__ import annotations

import unittest
from collections import namedtuple
from datetime import date

from stock_harness.config import TushareSettings, UniverseSymbol
from stock_harness.models import InstrumentKind
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


class _UniverseClient:
    def fund_basic(self, **kwargs):
        if kwargs["status"] != "L":
            return []
        return [{
            "ts_code": "510300.SH", "name": "CSI 300 ETF", "status": "L",
            "list_date": "20120528", "delist_date": None,
        }]

    def index_basic(self, **kwargs):
        if kwargs["market"] != "SSE":
            return []
        return [{
            "ts_code": "000300.SH", "name": "CSI 300", "market": "SSE",
            "list_date": "20050408",
        }]

    def index_classify(self, **_kwargs):
        return [{
            "index_code": "801010.SI", "industry_name": "Agriculture",
            "level": "L1", "src": "SW2021",
        }]

    def fund_daily(self, **kwargs):
        return [self._bar("510300.SH", kwargs["end_date"], 12.34)]

    def index_daily(self, **kwargs):
        return [self._bar("000300.SH", kwargs["end_date"], 56.78)]

    def sw_daily(self, **kwargs):
        return [self._bar("801010.SI", kwargs["end_date"], 9.87)]

    @staticmethod
    def _bar(symbol, trade_date, volume):
        return {
            "ts_code": symbol, "trade_date": trade_date,
            "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0,
            "vol": volume,
        }


class _IncompleteIndexClient(_UniverseClient):
    def index_daily(self, **kwargs):
        complete = self._bar("000300.SH", kwargs["end_date"], 56.78)
        incomplete = dict(complete, trade_date=kwargs["start_date"], open=None)
        return [complete, incomplete]


class _InvalidSectorClient(_UniverseClient):
    def sw_daily(self, **kwargs):
        row = self._bar("801010.SI", kwargs["end_date"], 9.87)
        row["close"] = 12.01
        return [row]


class _ExpandedCatalogClient(_UniverseClient):
    def fund_basic(self, **kwargs):
        if kwargs["status"] == "D":
            return []
        return [
            {
                "ts_code": "512480.SH", "name": "Semiconductor ETF",
                "fund_type": "股票型", "list_date": "20190508",
                "delist_date": None, "status": "L", "market": "E",
            },
            {
                "ts_code": "511010.SH", "name": "Bond ETF",
                "fund_type": "债券型", "list_date": "20130325",
                "delist_date": None, "status": "L", "market": "E",
            },
        ]

    def dc_index(self, **kwargs):
        if kwargs["trade_date"] != "20260731":
            return []
        return [{"ts_code": "BK1128.DC", "trade_date": "20260731", "name": "CPO"}]

    def dc_daily(self, **kwargs):
        if "trade_date" in kwargs:
            return [{"ts_code": "BK1128.DC", "trade_date": "20260731", "category": "概念板块"}]
        return [self._bar("BK1128.DC", kwargs["end_date"], 12345)]

    def ths_index(self, **_kwargs):
        return [{
            "ts_code": "886033.TI", "name": "CPO", "count": 203,
            "exchange": "A", "list_date": "20230210", "type": "N",
        }]

    def ths_daily(self, **kwargs):
        return [self._bar("886033.TI", kwargs["end_date"], 67890)]

    def dc_member(self, **_kwargs):
        return [{
            "trade_date": "20260731", "ts_code": "BK1128.DC",
            "con_code": "300308.SZ", "name": "Zhongji Innolight",
        }]

    def ths_member(self, **_kwargs):
        return [{
            "ts_code": "886033.TI", "con_code": "300308.SZ",
            "con_name": "Zhongji Innolight",
        }]

    def daily(self, **kwargs):
        return [{"ts_code": "600519.SH", "trade_date": kwargs["trade_date"], "pct_chg": 3.5}]

    def daily_basic(self, **kwargs):
        return [{"ts_code": "600519.SH", "trade_date": kwargs["trade_date"], "total_mv": 20_000}]

    def etf_sh_cons(self, **kwargs):
        return [{
            "trade_date": kwargs["trade_date"], "ts_code": kwargs["ts_code"],
            "con_code": "600519.SH", "con_name": "Moutai", "qty": 100,
            "exchange": "SH",
        }]

    def etf_sz_cons(self, **kwargs):
        return [{
            "trade_date": kwargs["trade_date"], "ts_code": kwargs["ts_code"],
            "con_code": "159900.SZ", "con_name": "申赎现金", "qty": 0,
            "exchange": "SZ",
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

    def test_lists_configured_etfs_indices_and_sw_l1_sectors(self) -> None:
        provider = TushareDailyProvider(_settings(), client=_UniverseClient())
        etf = UniverseSymbol("510300.SH", "broad", "representative tracker")
        index = UniverseSymbol("000300.SH", "broad", "market benchmark")

        self.assertEqual(provider.list_etfs([etf])[0].kind, InstrumentKind.ETF)
        self.assertEqual(
            provider.list_broad_indices([index])[0].kind, InstrumentKind.INDEX
        )
        self.assertEqual(
            provider.list_sectors("SW2021", "L1")[0].kind, InstrumentKind.SECTOR
        )

    def test_configured_symbol_volume_units_are_normalized_to_shares(self) -> None:
        provider = TushareDailyProvider(_settings(), client=_UniverseClient())
        target = date(2026, 7, 31)

        etf = provider.fetch_symbol_daily_bars(
            InstrumentKind.ETF, "510300.SH", target, target
        )[0]
        index = provider.fetch_symbol_daily_bars(
            InstrumentKind.INDEX, "000300.SH", target, target
        )[0]
        sector = provider.fetch_symbol_daily_bars(
            InstrumentKind.SECTOR, "801010.SI", target, target
        )[0]

        self.assertEqual((etf.volume, index.volume, sector.volume), (1_234, 5_678, 98_700))

    def test_missing_configured_symbol_is_rejected(self) -> None:
        provider = TushareDailyProvider(_settings(), client=_UniverseClient())
        missing = UniverseSymbol("510500.SH", "broad", "missing fixture")

        with self.assertRaisesRegex(RuntimeError, "510500.SH"):
            provider.list_etfs([missing])

    def test_close_only_index_history_is_not_fabricated_into_ohlc(self) -> None:
        provider = TushareDailyProvider(_settings(), client=_IncompleteIndexClient())

        bars = provider.fetch_symbol_daily_bars(
            InstrumentKind.INDEX,
            "000300.SH",
            date(2026, 7, 30),
            date(2026, 7, 31),
        )

        self.assertEqual([bar.trade_date for bar in bars], [date(2026, 7, 31)])

    def test_invalid_sector_envelope_is_exposed_for_targeted_repair(self) -> None:
        provider = TushareDailyProvider(_settings(), client=_InvalidSectorClient())

        bars = provider.fetch_symbol_daily_bars(
            InstrumentKind.SECTOR,
            "801010.SI",
            date(2026, 7, 31),
            date(2026, 7, 31),
        )

        self.assertEqual(bars, [])
        self.assertEqual(provider.rejected_bars[0].symbol, "801010.SI")

    def test_expanded_catalogs_keep_etf_dc_and_ths_identities_separate(self) -> None:
        provider = TushareDailyProvider(_settings(), client=_ExpandedCatalogClient())
        observed_on = date(2026, 8, 2)

        etfs = provider.list_all_equity_etfs(observed_on)
        dc = provider.list_dc_boards(observed_on)
        ths = provider.list_ths_boards(observed_on)

        self.assertEqual([item.provider_symbol for item in etfs], ["512480.SH"])
        self.assertEqual((dc[0].provider_symbol, dc[0].source_system), ("BK1128.DC", "eastmoney"))
        self.assertEqual((ths[0].provider_symbol, ths[0].source_system), ("886033.TI", "ths"))
        self.assertEqual(ths[0].constituent_count, 203)

    def test_board_daily_and_membership_provenance_is_preserved(self) -> None:
        provider = TushareDailyProvider(_settings(), client=_ExpandedCatalogClient())
        target = date(2026, 7, 31)

        dc_bar = provider.fetch_board_daily_bars("eastmoney", "BK1128.DC", target, target)[0]
        ths_bar = provider.fetch_board_daily_bars("ths", "886033.TI", target, target)[0]
        dc_member = provider.list_board_members("eastmoney", "BK1128.DC", target)[0]
        ths_member = provider.list_board_members("ths", "886033.TI", target)[0]

        self.assertEqual((dc_bar.volume, ths_bar.volume), (12345, 6_789_000))
        self.assertEqual((dc_member.source, ths_member.source), ("tushare_dc", "tushare_ths"))

    def test_market_snapshot_converts_total_market_cap_to_cny(self) -> None:
        provider = TushareDailyProvider(_settings(), client=_ExpandedCatalogClient())

        snapshot = provider.fetch_stock_market_snapshots(date(2026, 8, 3))[0]

        self.assertEqual(snapshot.change_percent, 3.5)
        self.assertEqual(snapshot.total_market_cap, 200_000_000)

    def test_etf_holdings_use_dated_exchange_pcf_and_filter_cash(self) -> None:
        provider = TushareDailyProvider(_settings(), client=_ExpandedCatalogClient())
        target = date(2026, 8, 3)

        as_of, sh = provider.fetch_etf_holdings("510300.SH", [target])
        _, sz = provider.fetch_etf_holdings("159915.SZ", [target])

        self.assertEqual(as_of, target)
        self.assertEqual((sh[0].holding_symbol, sh[0].quantity), ("600519.SH", 100))
        self.assertEqual(sz, [])


if __name__ == "__main__":
    unittest.main()
