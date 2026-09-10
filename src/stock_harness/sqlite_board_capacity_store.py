"""Read-only constituent turnover capacity aggregates for boards."""

from __future__ import annotations

from datetime import date, timedelta

from stock_harness.sqlite_mapping import _date_key


class SQLiteBoardCapacityStoreMixin:
    def calculate_board_capacity_snapshots(
        self, effective_date: date,
    ) -> dict[str, dict[str, object]]:
        lower = effective_date - timedelta(days=60)
        with self._lock:
            rows = self._connection.execute(
                """
                WITH recent AS MATERIALIZED (
                    SELECT bar.instrument_id, bar.close * bar.volume AS turnover,
                           row_number() OVER (
                               PARTITION BY bar.instrument_id ORDER BY bar.trade_date DESC
                           ) AS rn
                    FROM daily_bars AS bar
                    JOIN instruments AS stock USING (instrument_id)
                    WHERE stock.kind = 'stock'
                      AND bar.trade_date BETWEEN ? AND ?
                ), stock_capacity AS MATERIALIZED (
                    SELECT instrument_id,
                           avg(CASE WHEN rn <= 5 THEN turnover END) AS recent_5,
                           avg(CASE WHEN rn BETWEEN 6 AND 25 THEN turnover END) AS baseline_20,
                           avg(CASE WHEN rn <= 20 THEN turnover END) AS capacity_20,
                           count(CASE WHEN rn <= 20 THEN 1 END) AS covered_sessions
                    FROM recent WHERE rn <= 25 GROUP BY instrument_id
                ), memberships AS MATERIALIZED (
                    SELECT DISTINCT membership.board_instrument_id,
                                    stock.instrument_id AS stock_instrument_id
                    FROM board_memberships AS membership
                    JOIN instruments AS board
                      ON board.instrument_id = membership.board_instrument_id
                    JOIN instruments AS stock
                      ON stock.symbol = membership.member_symbol
                    WHERE membership.active = 1 AND board.active = 1
                      AND board.kind = 'sector' AND stock.kind = 'stock'
                )
                SELECT board.symbol, count(*), count(capacity.capacity_20),
                       sum(capacity.recent_5), sum(capacity.baseline_20),
                       sum(capacity.capacity_20),
                       sum(capacity.capacity_20 * capacity.capacity_20),
                       max(capacity.capacity_20),
                       avg(capacity.covered_sessions)
                FROM memberships
                JOIN instruments AS board
                  ON board.instrument_id = memberships.board_instrument_id
                LEFT JOIN stock_capacity AS capacity
                  ON capacity.instrument_id = memberships.stock_instrument_id
                GROUP BY memberships.board_instrument_id, board.symbol
                """,
                (_date_key(lower), _date_key(effective_date)),
            ).fetchall()
        result: dict[str, dict[str, object]] = {}
        for row in rows:
            member_count = int(row[1])
            covered = int(row[2])
            recent = float(row[3]) if row[3] is not None else None
            baseline = float(row[4]) if row[4] is not None else None
            capacity = float(row[5]) if row[5] is not None else None
            squared = float(row[6]) if row[6] is not None else None
            largest = float(row[7]) if row[7] is not None else None
            result[str(row[0])] = {
                "member_count": member_count,
                "covered_member_count": covered,
                "coverage_ratio": covered / member_count if member_count else 0.0,
                "turnover_recent_5": recent,
                "turnover_baseline_20": baseline,
                "turnover_capacity_20": capacity,
                "turnover_intensity": (
                    recent / baseline if recent is not None and baseline else None
                ),
                "turnover_concentration_hhi": (
                    squared / (capacity * capacity)
                    if squared is not None and capacity else None
                ),
                "largest_member_share": (
                    largest / capacity if largest is not None and capacity else None
                ),
                "average_covered_sessions": (
                    float(row[8]) if row[8] is not None else None
                ),
                "membership_semantics": "current-active-membership",
            }
        return result
