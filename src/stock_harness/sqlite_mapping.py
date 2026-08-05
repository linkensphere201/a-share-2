"""SQLite key conversion and result-row mapping helpers."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import date
from hashlib import blake2b

from stock_harness.models import DailyBar


def _date_key(value: date) -> int:
    return value.year * 10_000 + value.month * 100 + value.day


def _date_from_key(value: int) -> date:
    return date(value // 10_000, value // 100 % 100, value % 100)


def _source_profile(source: str) -> tuple[str, str]:
    if source == "tushare_dc":
        return "tushare", "eastmoney"
    if source == "tushare_ths":
        return "tushare", "ths"
    if source.startswith("akshare_eastmoney"):
        return "akshare", "eastmoney"
    if source.startswith("akshare_ths"):
        return "akshare", "ths"
    return source, source


def _instrument_row(row: sqlite3.Row) -> dict[str, object]:
    classification = _instrument_classification(
        str(row[2]), str(row[3]), row[5], row[7]
    )
    return {
        "symbol": str(row[0]), "name": str(row[1]), "kind": str(row[2]),
        "exchange": str(row[3]), "active": bool(row[4]),
        "source_system": row[5], "family": row[6], "category": row[7],
        "classification": classification,
        "classification_label": {
            "stock": "个股", "etf": "ETF", "index": "指数",
            "concept": "概念板块", "industry": "行业板块", "sector": "其他板块",
        }[classification],
        "source_label": _instrument_source_label(
            classification, str(row[3]), row[5]
        ),
        "first_trade_date": _date_from_key(int(row[8])) if row[8] is not None else None,
        "last_trade_date": _date_from_key(int(row[9])) if row[9] is not None else None,
        "rows": int(row[10]),
    }


def _instrument_classification_clause(classification: str) -> tuple[str, list[object]]:
    if classification in {"stock", "etf", "index"}:
        return "instrument.kind = ?", [classification]
    concept = (
        "(instrument.kind = 'sector' AND ("
        "catalog.category IN ('概念板块', 'concept') OR "
        "(catalog.source_system = 'ths' AND catalog.category = 'N')))"
    )
    industry = (
        "(instrument.kind = 'sector' AND (instrument.exchange = 'SI' OR "
        "catalog.category IN ('行业板块', 'industry') OR "
        "(catalog.source_system = 'ths' AND catalog.category = 'I')))"
    )
    if classification == "concept":
        return concept, []
    if classification == "industry":
        return industry, []
    return f"(instrument.kind = 'sector' AND NOT {concept} AND NOT {industry})", []


def _instrument_classification(
    kind: str,
    exchange: str,
    source_system: object,
    category: object,
) -> str:
    if kind != "sector":
        return kind
    category_value = str(category or "")
    source_value = str(source_system or "")
    if category_value in {"概念板块", "concept"} or (
        source_value == "ths" and category_value == "N"
    ):
        return "concept"
    if exchange == "SI" or category_value in {"行业板块", "industry"} or (
        source_value == "ths" and category_value == "I"
    ):
        return "industry"
    return "sector"


def _instrument_source_label(
    classification: str, exchange: str, source_system: object
) -> str:
    source_value = str(source_system or "")
    if source_value == "eastmoney":
        return "东财"
    if source_value == "ths":
        return "同花顺"
    if exchange == "SI":
        return "申万"
    if classification in {"etf", "stock"}:
        return {"SH": "上交所", "SZ": "深交所", "BJ": "北交所"}.get(
            exchange, exchange
        )
    if classification == "index":
        return {"CSI": "中证", "SH": "上证", "SZ": "深证"}.get(
            exchange, "主要指数"
        )
    return "其他"


def _snapshot_hash(bars: Sequence[DailyBar]) -> bytes:
    digest = blake2b(digest_size=16)
    for bar in sorted(bars, key=lambda item: item.symbol):
        digest.update(
            f"{bar.symbol}|{_date_key(bar.trade_date)}|{bar.open:.12g}|{bar.high:.12g}|"
            f"{bar.low:.12g}|{bar.close:.12g}|{bar.volume}\n".encode("ascii")
        )
    return digest.digest()
