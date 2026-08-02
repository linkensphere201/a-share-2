"""Independent free-provider adapters used only for OHLCV validation."""

from __future__ import annotations

from datetime import date
from typing import Any

from stock_harness.models import DailyBar


class AkShareEastmoneyValidationProvider:
    code = "akshare_eastmoney"

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def fetch_symbol_daily_bars(
        self, symbol: str, start_date: date, end_date: date
    ) -> list[DailyBar]:
        client = self._get_client()
        frame = client.stock_zh_a_hist(
            symbol=symbol.split(".", 1)[0],
            period="daily",
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            adjust="",
        )
        if frame is None or frame.empty:
            return []
        return [
            DailyBar(
                symbol=symbol,
                trade_date=date.fromisoformat(str(row["日期"])[:10]),
                open=float(row["开盘"]),
                high=float(row["最高"]),
                low=float(row["最低"]),
                close=float(row["收盘"]),
                # Eastmoney stock-history volume is reported in lots.
                volume=int(round(float(row["成交量"]) * 100)),
            )
            for row in frame.to_dict("records")
        ]

    def _get_client(self) -> Any:
        if self._client is None:
            import akshare

            self._client = akshare
        return self._client


class AkShareSinaValidationProvider:
    code = "akshare_sina"

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def fetch_symbol_daily_bars(
        self, symbol: str, start_date: date, end_date: date
    ) -> list[DailyBar]:
        number, exchange = symbol.split(".", 1)
        client = self._get_client()
        frame = client.stock_zh_a_daily(
            symbol=f"{exchange.lower()}{number}",
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            adjust="",
        )
        if frame is None or frame.empty:
            return []
        return [
            DailyBar(
                symbol=symbol,
                trade_date=date.fromisoformat(str(row["date"])[:10]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                # Sina's stock_zh_a_daily volume is expressed in shares.
                volume=int(round(float(row["volume"]))),
            )
            for row in frame.to_dict("records")
        ]

    def _get_client(self) -> Any:
        if self._client is None:
            import akshare

            self._client = akshare
        return self._client


class BaostockValidationProvider:
    code = "baostock"

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def fetch_symbol_daily_bars(
        self, symbol: str, start_date: date, end_date: date
    ) -> list[DailyBar]:
        client = self._get_client()
        login = client.login()
        if login.error_code != "0":
            raise RuntimeError(f"baostock login failed: {login.error_code} {login.error_msg}")
        try:
            result = client.query_history_k_data_plus(
                _baostock_symbol(symbol),
                "date,open,high,low,close,volume,tradestatus",
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                frequency="d",
                adjustflag="3",
            )
            if result.error_code != "0":
                raise RuntimeError(
                    f"baostock query failed: {result.error_code} {result.error_msg}"
                )
            bars = []
            while result.next():
                row = dict(zip(result.fields, result.get_row_data(), strict=True))
                if row.get("tradestatus") != "1" or not row.get("open"):
                    continue
                bars.append(
                    DailyBar(
                        symbol=symbol,
                        trade_date=date.fromisoformat(row["date"]),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        # Baostock daily volume is already expressed in shares.
                        volume=int(float(row["volume"])),
                    )
                )
            return bars
        finally:
            client.logout()

    def _get_client(self) -> Any:
        if self._client is None:
            import baostock

            self._client = baostock
        return self._client


def _baostock_symbol(symbol: str) -> str:
    code, exchange = symbol.split(".", 1)
    market = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(exchange.upper())
    if market is None:
        raise ValueError(f"unsupported exchange for Baostock: {symbol}")
    return f"{market}.{code}"
