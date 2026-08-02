"""Provider adapters for the asynchronous Tushare gap-repair channel."""

from __future__ import annotations

from datetime import date
from typing import Any

from stock_harness.models import DailyBar, Instrument, InstrumentKind, RepairBatch


class BaostockTradeDateRepairProvider:
    code = "baostock"

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def fetch_stock_trade_date(self, trade_date: date) -> RepairBatch:
        client = self._get_client()
        login = client.login()
        if login.error_code != "0":
            raise RuntimeError(f"baostock login failed: {login.error_code} {login.error_msg}")
        try:
            universe = client.query_all_stock(trade_date.isoformat())
            _check_result(universe, "query_all_stock")
            instruments = []
            while universe.next():
                row = dict(zip(universe.fields, universe.get_row_data(), strict=True))
                if row.get("tradeStatus") != "1" or not _is_a_share(row["code"]):
                    continue
                symbol = _stock_harness_symbol(row["code"])
                instruments.append(
                    Instrument(
                        symbol=symbol,
                        name=row.get("code_name") or symbol,
                        kind=InstrumentKind.STOCK,
                        exchange=symbol.rsplit(".", 1)[-1],
                        active=False,
                    )
                )

            bars = []
            failed = []
            for instrument in instruments:
                try:
                    bar = self._fetch_one(instrument.symbol, trade_date)
                except Exception:
                    failed.append(instrument.symbol)
                    continue
                if bar is None:
                    failed.append(instrument.symbol)
                else:
                    bars.append(bar)
            return RepairBatch(tuple(instruments), tuple(bars), tuple(failed))
        finally:
            client.logout()

    def _fetch_one(self, symbol: str, trade_date: date) -> DailyBar | None:
        client = self._get_client()
        result = client.query_history_k_data_plus(
            _baostock_symbol(symbol),
            "date,open,high,low,close,volume,tradestatus",
            start_date=trade_date.isoformat(),
            end_date=trade_date.isoformat(),
            frequency="d",
            adjustflag="3",
        )
        _check_result(result, f"query_history_k_data_plus({symbol})")
        if not result.next():
            return None
        row = dict(zip(result.fields, result.get_row_data(), strict=True))
        if row.get("tradestatus") != "1" or not row.get("open"):
            return None
        return DailyBar(
            symbol=symbol,
            trade_date=date.fromisoformat(row["date"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=int(float(row["volume"])),
        )

    def _get_client(self) -> Any:
        if self._client is None:
            import baostock

            self._client = baostock
        return self._client


def _check_result(result: Any, operation: str) -> None:
    if result.error_code != "0":
        raise RuntimeError(f"baostock {operation} failed: {result.error_code} {result.error_msg}")


def _is_a_share(code: str) -> bool:
    market, number = code.lower().split(".", 1)
    if market == "sh":
        return number.startswith(("600", "601", "603", "605", "688", "689"))
    if market == "sz":
        return number.startswith(("000", "001", "002", "003", "300", "301"))
    if market == "bj":
        return number.startswith(("4", "8", "9"))
    return False


def _stock_harness_symbol(code: str) -> str:
    market, number = code.split(".", 1)
    return f"{number}.{market.upper()}"


def _baostock_symbol(symbol: str) -> str:
    number, market = symbol.split(".", 1)
    return f"{market.lower()}.{number}"
