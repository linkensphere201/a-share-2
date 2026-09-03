"""ETF holding persistence for the SQLite market data store."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timezone

from stock_harness.models import EtfHolding, InstrumentKind
from stock_harness.sqlite_mapping import _date_from_key, _date_key


class SQLiteEtfHoldingStoreMixin:
    def replace_etf_holdings(
        self,
        source: str,
        etf_symbol: str,
        as_of_date: date,
        holdings: Sequence[EtfHolding],
    ) -> int:
        for item in holdings:
            if item.etf_symbol != etf_symbol or item.as_of_date != as_of_date:
                raise ValueError("ETF holdings must share ETF symbol and as-of date")
        if len({item.holding_symbol for item in holdings}) != len(holdings):
            raise ValueError("ETF holdings must contain unique symbols")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            etf_row = self._connection.execute(
                "SELECT instrument_id, kind FROM instruments WHERE symbol = ?", (etf_symbol,)
            ).fetchone()
            if etf_row is None or str(etf_row[1]) != InstrumentKind.ETF.value:
                raise ValueError(f"unknown ETF instrument: {etf_symbol}")
            source_id = self._source_id(source)
            etf_id = int(etf_row[0])
            as_of_key = _date_key(as_of_date)
            self._connection.execute(
                "DELETE FROM etf_holdings WHERE source_id = ? AND etf_instrument_id = ? AND as_of_date = ?",
                (source_id, etf_id, as_of_key),
            )
            self._connection.executemany(
                """
                INSERT INTO etf_holdings(
                    source_id, etf_instrument_id, as_of_date, holding_symbol,
                    holding_name, quantity, weight_percent, market_value,
                    holding_rank, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        source_id, etf_id, as_of_key, item.holding_symbol,
                        item.holding_name, item.quantity, item.weight_percent,
                        item.market_value, item.rank, now_ms,
                    )
                    for item in holdings
                ),
            )
        return len(holdings)

    def record_etf_holding_receipt(
        self,
        source: str,
        etf_symbol: str,
        requested_date: date,
        as_of_date: date | None,
        row_count: int,
    ) -> None:
        if row_count < 0:
            raise ValueError("ETF holding receipt row count must be non-negative")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():

            row = self._connection.execute(
                "SELECT instrument_id, kind FROM instruments WHERE symbol = ?", (etf_symbol,)
            ).fetchone()
            if row is None or str(row[1]) != InstrumentKind.ETF.value:
                raise ValueError(f"unknown ETF instrument: {etf_symbol}")
            source_id = self._source_id(source)
            self._connection.execute(
                """
                INSERT INTO etf_holding_receipts(
                    source_id, etf_instrument_id, requested_date, as_of_date,
                    row_count, status, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, etf_instrument_id, requested_date) DO UPDATE SET
                    as_of_date = excluded.as_of_date,
                    row_count = excluded.row_count,
                    status = excluded.status,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    source_id, int(row[0]), _date_key(requested_date),
                    _date_key(as_of_date) if as_of_date else None, row_count,
                    "complete" if row_count else "empty", now_ms,
                ),
            )

    def list_etfs_needing_holding_refresh(
        self,
        source: str,
        requested_date: date,
        limit: int,
        preferred_symbols: Sequence[str] = (),
    ) -> list[str]:
        if limit <= 0:
            return []
        preferred = list(dict.fromkeys(item.upper() for item in preferred_symbols))
        with self._lock, self._transaction():
            source_id = self._source_id(source)
            if preferred:
                placeholders = ",".join("?" for _ in preferred)
                priority = f"CASE WHEN instrument.symbol IN ({placeholders}) THEN 0 ELSE 1 END"
                params: list[object] = [source_id, _date_key(requested_date), *preferred, limit]
            else:
                priority = "1"
                params = [source_id, _date_key(requested_date), limit]
            rows = self._connection.execute(
                f"""
                SELECT instrument.symbol
                FROM instruments AS instrument
                LEFT JOIN etf_holding_receipts AS receipt
                  ON receipt.etf_instrument_id = instrument.instrument_id
                 AND receipt.source_id = ? AND receipt.requested_date = ?
                WHERE instrument.kind = 'etf' AND instrument.active = 1
                  AND receipt.etf_instrument_id IS NULL
                ORDER BY {priority}, instrument.symbol
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [str(row[0]) for row in rows]

    def list_etf_holdings(
        self, etf_symbol: str, limit: int = 500, offset: int = 0
    ) -> dict[str, object] | None:
        if not 1 <= limit <= 5000 or offset < 0:
            raise ValueError("invalid ETF holding pagination")
        with self._lock:
            meta = self._connection.execute(
                """
                SELECT holding.as_of_date, source.code, count(*)
                FROM etf_holdings AS holding
                JOIN instruments AS etf ON etf.instrument_id = holding.etf_instrument_id
                JOIN sources AS source USING (source_id)
                WHERE etf.symbol = ?
                GROUP BY holding.as_of_date, source.code
                ORDER BY holding.as_of_date DESC,
                         CASE source.code WHEN 'tushare_etf_pcf' THEN 0 ELSE 1 END,
                         source.code
                LIMIT 1
                """,
                (etf_symbol,),
            ).fetchone()
            if meta is None:
                return None
            rows = self._connection.execute(
                """
                SELECT holding.holding_symbol, holding.holding_name,
                       holding.quantity, holding.weight_percent,
                       holding.market_value, holding.holding_rank,
                       instrument.kind, instrument.exchange, instrument.active
                FROM etf_holdings AS holding
                JOIN instruments AS etf ON etf.instrument_id = holding.etf_instrument_id
                JOIN sources AS source USING (source_id)
                LEFT JOIN instruments AS instrument
                  ON instrument.symbol = holding.holding_symbol
                WHERE etf.symbol = ? AND holding.as_of_date = ? AND source.code = ?
                ORDER BY coalesce(holding.holding_rank, 2147483647), holding.holding_symbol
                LIMIT ? OFFSET ?
                """,
                (etf_symbol, int(meta[0]), str(meta[1]), limit, offset),
            ).fetchall()
        return {
            "symbol": etf_symbol,
            "as_of_date": _date_from_key(int(meta[0])),
            "source": str(meta[1]),
            "total": int(meta[2]),
            "items": [
                {
                    "symbol": str(row[0]), "name": str(row[1]),
                    "quantity": float(row[2]) if row[2] is not None else None,
                    "weight_percent": float(row[3]) if row[3] is not None else None,
                    "market_value": float(row[4]) if row[4] is not None else None,
                    "rank": int(row[5]) if row[5] is not None else None,
                    "kind": str(row[6]) if row[6] is not None else None,
                    "exchange": str(row[7]) if row[7] is not None else None,
                    "available": row[6] is not None and bool(row[8]),
                }
                for row in rows
            ],
        }


