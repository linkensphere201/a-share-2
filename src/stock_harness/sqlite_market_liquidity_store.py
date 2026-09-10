"""Read-only broad-market liquidity aggregates."""

from __future__ import annotations

from datetime import date

from stock_harness.sqlite_mapping import _date_from_key, _date_key


class SQLiteMarketLiquidityStoreMixin:
    def get_market_turnover_range(
        self, start_date: date, end_date: date,
    ) -> list[tuple[date, float]]:
        """Aggregate each market day once for replay and historical analysis."""
        if end_date < start_date:
            raise ValueError("market turnover range end must not precede start")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT bar.trade_date, sum(bar.close * bar.volume)
                FROM daily_bars AS bar
                JOIN instruments AS stock USING (instrument_id)
                WHERE stock.kind = 'stock'
                  AND bar.trade_date BETWEEN ? AND ?
                GROUP BY bar.trade_date ORDER BY bar.trade_date
                """,
                (_date_key(start_date), _date_key(end_date)),
            ).fetchall()
        return [
            (_date_from_key(int(row[0])), float(row[1]))
            for row in rows if row[1] is not None
        ]

    def get_market_turnover_proxy(
        self, effective_date: date, sessions: int = 25,
    ) -> list[float]:
        if not 2 <= sessions <= 60:
            raise ValueError("market turnover sessions must be between 2 and 60")
        with self._lock:
            rows = self._connection.execute(
                """
                WITH dates AS MATERIALIZED (
                    SELECT DISTINCT bar.trade_date
                    FROM daily_bars AS bar
                    JOIN instruments AS stock USING (instrument_id)
                    WHERE stock.kind = 'stock'
                      AND bar.trade_date <= ?
                    ORDER BY bar.trade_date DESC LIMIT ?
                )
                SELECT bar.trade_date, sum(bar.close * bar.volume)
                FROM dates
                JOIN daily_bars AS bar USING (trade_date)
                JOIN instruments AS stock USING (instrument_id)
                WHERE stock.kind = 'stock'
                GROUP BY bar.trade_date ORDER BY bar.trade_date
                """,
                (_date_key(effective_date), sessions),
            ).fetchall()
        return [float(row[1]) for row in rows if row[1] is not None]
