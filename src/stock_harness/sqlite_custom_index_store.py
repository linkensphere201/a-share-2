"""Materialized custom-index SQLite storage mixin."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timezone
import json
import sqlite3

from stock_harness.custom_index import (
    CALCULATION_VERSION,
    ConstituentInput,
    base_bar,
    calculate_bar,
    normalize_members,
)
from stock_harness.models import DailyBar, InstrumentKind
from stock_harness.search_terms import matches_name_or_pinyin
from stock_harness.sqlite_mapping import _date_from_key, _date_key


class SQLiteCustomIndexStoreMixin:
    def create_custom_index(
        self,
        index_id: str,
        name: str,
        description: str,
        base_date: date,
        base_value: float,
        weighting_method: str,
        members: Sequence[dict[str, object]],
    ) -> dict[str, object]:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("custom index name is required")
        if base_value <= 0:
            raise ValueError("custom index base value must be positive")
        if weighting_method not in {"equal", "manual"}:
            raise ValueError("invalid custom index weighting method")
        normalized = normalize_members(
            [(str(item.get("symbol", "")), item.get("weight")) for item in members],
            weighting_method,
        )
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        symbol = f"CINDEX:{index_id.lower()}"
        with self._lock, self._transaction():
            instrument_ids = self._instrument_ids({item.symbol for item in normalized})
            missing = {item.symbol for item in normalized} - instrument_ids.keys()
            if missing:
                raise ValueError("unknown custom index members: " + ", ".join(sorted(missing)))
            invalid = self._connection.execute(
                "SELECT symbol FROM instruments WHERE instrument_id IN ("
                + ",".join("?" for _ in instrument_ids)
                + ") AND kind <> ? LIMIT 1",
                (*instrument_ids.values(), InstrumentKind.STOCK.value),
            ).fetchone()
            if invalid is not None:
                raise ValueError(f"custom index members must be stocks: {invalid[0]}")
            self._connection.execute(
                "INSERT INTO instruments(symbol, name, kind, exchange, active) VALUES (?, ?, ?, 'LOCAL', 1)",
                (symbol, normalized_name, InstrumentKind.CUSTOM_INDEX.value),
            )
            custom_instrument_id = int(self._connection.execute(
                "SELECT instrument_id FROM instruments WHERE symbol = ?", (symbol,)
            ).fetchone()[0])
            self._connection.execute(
                """
                INSERT INTO custom_indices(
                    index_id, instrument_id, description, base_date, base_value,
                    status, calculation_version, created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    index_id.lower(), custom_instrument_id, description.strip(),
                    _date_key(base_date), base_value, CALCULATION_VERSION, now_ms, now_ms,
                ),
            )
            revision = self._connection.execute(
                """
                INSERT INTO custom_index_revisions(
                    index_id, revision_number, effective_from, weighting_method, created_at_ms
                ) VALUES (?, 1, ?, ?, ?) RETURNING revision_id
                """,
                (index_id.lower(), _date_key(base_date), weighting_method, now_ms),
            ).fetchone()
            revision_id = int(revision[0])
            self._connection.executemany(
                """
                INSERT INTO custom_index_revision_members(
                    revision_id, instrument_id, position, raw_weight, normalized_weight
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (
                        revision_id, instrument_ids[item.symbol], position,
                        item.raw_weight, item.normalized_weight,
                    )
                    for position, item in enumerate(normalized)
                ),
            )
        result = self.get_custom_index(index_id)
        assert result is not None
        return result

    def list_custom_indices(self, query: str = "") -> list[dict[str, object]]:
        pattern = f"%{query.strip()}%"
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT custom.index_id, instrument.symbol, instrument.name,
                       custom.description, custom.base_date, custom.base_value,
                       custom.status, custom.last_error, custom.updated_at_ms,
                       (SELECT count(*) FROM custom_index_revision_members AS member
                        JOIN custom_index_revisions AS revision USING (revision_id)
                        WHERE revision.index_id = custom.index_id
                          AND revision.revision_number = (
                              SELECT max(revision_number) FROM custom_index_revisions
                              WHERE index_id = custom.index_id
                          )),
                       (SELECT min(trade_date) FROM custom_index_daily_bars WHERE index_id = custom.index_id),
                       (SELECT max(trade_date) FROM custom_index_daily_bars WHERE index_id = custom.index_id),
                       (SELECT count(*) FROM custom_index_daily_bars WHERE index_id = custom.index_id),
                       (SELECT count(*) FROM custom_index_daily_bars
                        WHERE index_id = custom.index_id AND quality_status <> 'complete')
                FROM custom_indices AS custom
                JOIN instruments AS instrument USING (instrument_id)
                WHERE (? = '%%' OR instrument.symbol LIKE ? OR instrument.name LIKE ?)
                ORDER BY instrument.name, instrument.symbol
                """,
                (pattern, pattern, pattern),
            ).fetchall()
        return [self._custom_index_summary(row) for row in rows]

    def update_custom_index(
        self,
        index_id: str,
        name: str,
        description: str,
        base_date: date,
        base_value: float,
        weighting_method: str,
        members: Sequence[dict[str, object]],
        effective_from: date,
    ) -> dict[str, object] | None:
        normalized_id = index_id.lower().removeprefix("cindex:")
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("custom index name is required")
        if base_value <= 0:
            raise ValueError("custom index base value must be positive")
        if weighting_method not in {"equal", "manual"}:
            raise ValueError("invalid custom index weighting method")
        normalized_members = normalize_members(
            [(str(item.get("symbol", "")), item.get("weight")) for item in members],
            weighting_method,
        )
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            custom = self._connection.execute(
                "SELECT instrument_id, base_date, base_value FROM custom_indices WHERE index_id = ?",
                (normalized_id,),
            ).fetchone()
            if custom is None:
                return None
            if _date_key(base_date) != int(custom[1]) or base_value != float(custom[2]):
                raise ValueError("custom index base date and value are immutable")
            if effective_from < base_date:
                raise ValueError("custom index revision cannot precede its base date")
            if effective_from > date.today():
                raise ValueError("custom index revision cannot start in the future")
            instrument_ids = self._instrument_ids({item.symbol for item in normalized_members})
            missing = {item.symbol for item in normalized_members} - instrument_ids.keys()
            if missing:
                raise ValueError("unknown custom index members: " + ", ".join(sorted(missing)))
            invalid = self._connection.execute(
                "SELECT symbol FROM instruments WHERE instrument_id IN ("
                + ",".join("?" for _ in instrument_ids)
                + ") AND kind <> ? LIMIT 1",
                (*instrument_ids.values(), InstrumentKind.STOCK.value),
            ).fetchone()
            if invalid is not None:
                raise ValueError(f"custom index members must be stocks: {invalid[0]}")
            revision_number = int(self._connection.execute(
                "SELECT coalesce(max(revision_number), 0) + 1 FROM custom_index_revisions WHERE index_id = ?",
                (normalized_id,),
            ).fetchone()[0])
            revision_id = int(self._connection.execute(
                """
                INSERT INTO custom_index_revisions(
                    index_id, revision_number, effective_from, weighting_method, created_at_ms
                ) VALUES (?, ?, ?, ?, ?) RETURNING revision_id
                """,
                (
                    normalized_id, revision_number, _date_key(effective_from),
                    weighting_method, now_ms,
                ),
            ).fetchone()[0])
            self._connection.executemany(
                """
                INSERT INTO custom_index_revision_members(
                    revision_id, instrument_id, position, raw_weight, normalized_weight
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (
                        revision_id, instrument_ids[item.symbol], position,
                        item.raw_weight, item.normalized_weight,
                    )
                    for position, item in enumerate(normalized_members)
                ),
            )
            self._connection.execute(
                "UPDATE instruments SET name = ? WHERE instrument_id = ?",
                (normalized_name, int(custom[0])),
            )
            self._connection.execute(
                """
                UPDATE custom_indices SET description = ?, status = 'pending',
                    last_error = NULL, updated_at_ms = ?
                WHERE index_id = ?
                """,
                (
                    description.strip(), now_ms, normalized_id,
                ),
            )
        return self.get_custom_index(normalized_id)

    def get_custom_index(self, index_id: str) -> dict[str, object] | None:
        normalized = index_id.lower().removeprefix("cindex:")
        with self._lock:
            row = self._connection.execute(
                """
                SELECT custom.index_id, instrument.symbol, instrument.name,
                       custom.description, custom.base_date, custom.base_value,
                       custom.status, custom.last_error, custom.updated_at_ms,
                       revision.revision_id, revision.revision_number,
                       revision.effective_from, revision.weighting_method
                FROM custom_indices AS custom
                JOIN instruments AS instrument USING (instrument_id)
                JOIN custom_index_revisions AS revision ON revision.index_id = custom.index_id
                WHERE custom.index_id = ?
                ORDER BY revision.revision_number DESC LIMIT 1
                """,
                (normalized,),
            ).fetchone()
            if row is None:
                return None
            members = self._connection.execute(
                """
                SELECT instrument.symbol, instrument.name, member.position,
                       member.raw_weight, member.normalized_weight
                FROM custom_index_revision_members AS member
                JOIN instruments AS instrument USING (instrument_id)
                WHERE member.revision_id = ? ORDER BY member.position
                """,
                (int(row[9]),),
            ).fetchall()
            coverage = self._connection.execute(
                """
                SELECT min(trade_date), max(trade_date), count(*),
                       count(*) FILTER (WHERE quality_status <> 'complete')
                FROM custom_index_daily_bars WHERE index_id = ?
                """,
                (normalized,),
            ).fetchone()
        return {
            "id": str(row[0]), "symbol": str(row[1]), "name": str(row[2]),
            "description": str(row[3]), "base_date": _date_from_key(int(row[4])),
            "base_value": float(row[5]), "status": str(row[6]), "last_error": row[7],
            "updated_at_ms": int(row[8]), "revision_number": int(row[10]),
            "effective_from": _date_from_key(int(row[11])), "weighting_method": str(row[12]),
            "members": [
                {
                    "symbol": str(item[0]), "name": str(item[1]), "position": int(item[2]),
                    "raw_weight": float(item[3]), "normalized_weight": float(item[4]),
                }
                for item in members
            ],
            "first_trade_date": _date_from_key(int(coverage[0])) if coverage[0] is not None else None,
            "last_trade_date": _date_from_key(int(coverage[1])) if coverage[1] is not None else None,
            "rows": int(coverage[2]),
            "quality_warning_days": int(coverage[3]),
        }

    def rebuild_custom_index(self, index_id: str, mode: str = "backfill") -> dict[str, object]:
        if mode not in {"backfill", "incremental", "correction"}:
            raise ValueError("invalid custom index calculation mode")
        normalized = index_id.lower().removeprefix("cindex:")
        detail = self.get_custom_index(normalized)
        if detail is None:
            raise ValueError("custom index not found")
        started_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock:
            dirty = self._connection.execute(
                "SELECT dirty_from FROM custom_index_dirty_dates WHERE index_id = ?",
                (normalized,),
            ).fetchone()
        calculation_mode = "correction" if dirty is not None else mode
        with self._lock, self._transaction():
            run = self._connection.execute(
                """
                INSERT INTO custom_index_calculation_runs(
                    index_id, mode, started_at_ms, row_count, status, message
                ) VALUES (?, ?, ?, 0, 'running', '') RETURNING run_id
                """,
                (normalized, calculation_mode, started_ms),
            ).fetchone()
            run_id = int(run[0])
            self._connection.execute(
                "UPDATE custom_indices SET status = 'building', last_error = NULL, updated_at_ms = ? WHERE index_id = ?",
                (started_ms, normalized),
            )
        try:
            incremental = calculation_mode == "incremental" and detail["rows"] > 0
            history = (
                self._calculate_custom_index_incremental(normalized)
                if incremental
                else self._calculate_custom_index_history(normalized)
            )
            bars = (
                [item for item in history if _date_key(item.trade_date) >= int(dirty[0])]
                if calculation_mode == "correction" and dirty is not None
                else history
            )
            completed_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            with self._lock, self._transaction():
                if calculation_mode == "correction" and dirty is not None:
                    self._connection.execute(
                        "DELETE FROM custom_index_daily_bars WHERE index_id = ? AND trade_date >= ?",
                        (normalized, int(dirty[0])),
                    )
                elif not incremental:
                    self._connection.execute("DELETE FROM custom_index_daily_bars WHERE index_id = ?", (normalized,))
                self._connection.executemany(
                    """
                    INSERT INTO custom_index_daily_bars(
                        index_id, trade_date, open, high, low, close, volume, daily_return,
                        eligible_count, total_count, quality_status, input_hash,
                        calculation_version, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(index_id, trade_date) DO UPDATE SET
                        open = excluded.open, high = excluded.high, low = excluded.low,
                        close = excluded.close, volume = excluded.volume,
                        daily_return = excluded.daily_return,
                        eligible_count = excluded.eligible_count,
                        total_count = excluded.total_count,
                        quality_status = excluded.quality_status,
                        input_hash = excluded.input_hash,
                        calculation_version = excluded.calculation_version,
                        updated_at_ms = excluded.updated_at_ms
                    """,
                    (
                        (
                            normalized, _date_key(item.trade_date), item.open, item.high,
                            item.low, item.close, item.volume, item.daily_return, item.eligible_count,
                            item.total_count, item.quality_status, item.input_hash,
                            CALCULATION_VERSION, completed_ms,
                        )
                        for item in bars
                    ),
                )
                self._connection.execute(
                    "UPDATE custom_indices SET status = 'ready', last_error = NULL, updated_at_ms = ? WHERE index_id = ?",
                    (completed_ms, normalized),
                )
                self._connection.execute(
                    """
                    UPDATE custom_index_calculation_runs SET completed_at_ms = ?,
                        from_date = ?, through_date = ?, row_count = ?, status = 'completed', message = 'materialized'
                    WHERE run_id = ?
                    """,
                    (
                        completed_ms,
                        _date_key(bars[0].trade_date) if bars else None,
                        _date_key(bars[-1].trade_date) if bars else None,
                        len(bars), run_id,
                    ),
                )
                self._refresh_custom_index_snapshot_locked(normalized, completed_ms)
                self._connection.execute(
                    "DELETE FROM custom_index_dirty_dates WHERE index_id = ?",
                    (normalized,),
                )
            result = self.get_custom_index(normalized)
            assert result is not None
            return result
        except Exception as exc:
            completed_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            message = str(exc)[:1000]
            with self._lock, self._transaction():
                self._connection.execute(
                    "UPDATE custom_indices SET status = 'error', last_error = ?, updated_at_ms = ? WHERE index_id = ?",
                    (message, completed_ms, normalized),
                )
                self._connection.execute(
                    "UPDATE custom_index_calculation_runs SET completed_at_ms = ?, status = 'failed', message = ? WHERE run_id = ?",
                    (completed_ms, message, run_id),
                )
            raise

    def mark_custom_index_error(self, index_id: str, message: str) -> None:
        normalized = index_id.lower().removeprefix("cindex:")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            self._connection.execute(
                """
                UPDATE custom_indices SET status = 'error', last_error = ?, updated_at_ms = ?
                WHERE index_id = ?
                """,
                (message[:1000], now_ms, normalized),
            )

    def has_dirty_custom_indices(self) -> bool:
        with self._lock:
            return self._connection.execute(
                "SELECT 1 FROM custom_index_dirty_dates LIMIT 1"
            ).fetchone() is not None

    def delete_custom_index(self, index_id: str) -> bool:
        normalized = index_id.lower().removeprefix("cindex:")
        with self._lock, self._transaction():
            row = self._connection.execute(
                "SELECT instrument_id FROM custom_indices WHERE index_id = ?", (normalized,)
            ).fetchone()
            if row is None:
                return False
            instrument_id = int(row[0])
            self._connection.execute("DELETE FROM custom_index_calculation_runs WHERE index_id = ?", (normalized,))
            self._connection.execute("DELETE FROM custom_index_dirty_dates WHERE index_id = ?", (normalized,))
            self._connection.execute("DELETE FROM custom_index_daily_bars WHERE index_id = ?", (normalized,))
            revision_ids = [int(item[0]) for item in self._connection.execute(
                "SELECT revision_id FROM custom_index_revisions WHERE index_id = ?", (normalized,)
            )]
            if revision_ids:
                self._connection.execute(
                    "DELETE FROM custom_index_revision_members WHERE revision_id IN ("
                    + ",".join("?" for _ in revision_ids) + ")",
                    revision_ids,
                )
            self._connection.execute("DELETE FROM custom_index_revisions WHERE index_id = ?", (normalized,))
            self._connection.execute("DELETE FROM custom_indices WHERE index_id = ?", (normalized,))
            self._connection.execute("DELETE FROM market_snapshots WHERE instrument_id = ?", (instrument_id,))
            self._connection.execute("DELETE FROM instruments WHERE instrument_id = ?", (instrument_id,))
        return True

    def _calculate_custom_index_history(self, index_id: str):
        with self._lock:
            custom = self._connection.execute(
                "SELECT base_date, base_value FROM custom_indices WHERE index_id = ?", (index_id,)
            ).fetchone()
            revisions = self._connection.execute(
                """
                SELECT revision_id, effective_from FROM custom_index_revisions
                WHERE index_id = ? ORDER BY effective_from, revision_number
                """,
                (index_id,),
            ).fetchall()
            revision_members: dict[int, list[sqlite3.Row]] = {}
            all_ids: set[int] = set()
            for revision in revisions:
                rows = self._connection.execute(
                    """
                    SELECT member.instrument_id, instrument.symbol, member.normalized_weight,
                           instrument.active,
                           (SELECT min(listed_on) FROM instrument_catalog_entries WHERE instrument_id = member.instrument_id),
                           (SELECT max(delisted_on) FROM instrument_catalog_entries WHERE instrument_id = member.instrument_id)
                    FROM custom_index_revision_members AS member
                    JOIN instruments AS instrument USING (instrument_id)
                    WHERE member.revision_id = ? ORDER BY member.position
                    """,
                    (int(revision[0]),),
                ).fetchall()
                revision_members[int(revision[0])] = rows
                all_ids.update(int(item[0]) for item in rows)
            if not all_ids:
                raise ValueError("custom index has no members")
            placeholders = ",".join("?" for _ in all_ids)
            completed_through = self._connection.execute(
                f"SELECT max(trade_date) FROM daily_bars WHERE instrument_id IN ({placeholders})",
                tuple(all_ids),
            ).fetchone()[0]
            if completed_through is None:
                raise ValueError("custom index members have no stored history")
            calendar_rows = self._connection.execute(
                f"""
                SELECT trade_date FROM (
                    SELECT trade_date FROM trading_calendar
                    WHERE trade_date >= ? AND trade_date <= ?
                    UNION
                    SELECT trade_date FROM daily_bars
                    WHERE instrument_id IN ({placeholders})
                      AND trade_date >= ? AND trade_date <= ?
                )
                ORDER BY trade_date
                """,
                (
                    int(custom[0]), int(completed_through), *all_ids,
                    int(custom[0]), int(completed_through),
                ),
            ).fetchall()
        calendar = [int(row[0]) for row in calendar_rows]
        if not calendar:
            with self._lock:
                fallback_rows = self._connection.execute(
                    f"""
                    SELECT DISTINCT trade_date FROM daily_bars
                    WHERE instrument_id IN ({placeholders}) AND trade_date >= ?
                    ORDER BY trade_date
                    """,
                    (*all_ids, int(custom[0])),
                ).fetchall()
            calendar = [int(row[0]) for row in fallback_rows]
        if not calendar:
            raise ValueError("custom index members have no history on or after base date")
        revision_index = 0
        previous: dict[int, tuple[float, float]] = {}
        output = []
        previous_index_close = float(custom[1])
        base_written = False
        for trade_key in calendar:
            while revision_index + 1 < len(revisions) and int(revisions[revision_index + 1][1]) <= trade_key:
                revision_index += 1
            current_revision_id = int(revisions[revision_index][0])
            current_members = revision_members[current_revision_id]
            with self._lock:
                day_rows = self._connection.execute(
                    f"""
                    SELECT bar.instrument_id, instrument.symbol, bar.trade_date,
                           bar.open, bar.high, bar.low, bar.close, bar.volume,
                           factor.factor
                    FROM daily_bars AS bar
                    JOIN instruments AS instrument USING (instrument_id)
                    LEFT JOIN stock_adjustment_factors AS factor
                      ON factor.instrument_id = bar.instrument_id
                     AND factor.trade_date = bar.trade_date
                    WHERE bar.instrument_id IN ({placeholders})
                      AND bar.trade_date = ?
                    ORDER BY bar.instrument_id
                    """,
                    (*all_ids, trade_key),
                ).fetchall()
                status_rows = self._connection.execute(
                    f"""
                    SELECT instrument_id, status FROM stock_trade_status
                    WHERE instrument_id IN ({placeholders}) AND trade_date = ?
                    """,
                    (*all_ids, trade_key),
                ).fetchall()
            today = {int(row[0]): row for row in day_rows}
            day_status = {int(row[0]): str(row[1]) for row in status_rows}
            for instrument_id, row in today.items():
                if row[8] is not None:
                    previous.setdefault(instrument_id, (float(row[6]), float(row[8])))
            if not base_written:
                current_ids = {int(member[0]) for member in current_members}
                day_volume = sum(
                    int(row[7]) for instrument_id, row in today.items()
                    if instrument_id in current_ids
                )
                item = base_bar(
                    _date_from_key(trade_key), previous_index_close,
                    len(current_members), day_volume,
                )
                output.append(item)
                base_written = True
                for instrument_id, row in today.items():
                    if row[8] is not None:
                        previous[instrument_id] = (float(row[6]), float(row[8]))
                continue
            constituents = []
            for member in current_members:
                instrument_id = int(member[0])
                row = today.get(instrument_id)
                prior = previous.get(instrument_id)
                listed_on = int(member[4]) if member[4] is not None else None
                delisted_on = int(member[5]) if member[5] is not None else None
                if (listed_on is not None and trade_key < listed_on) or (delisted_on is not None and trade_key > delisted_on):
                    status = "not_eligible"
                elif row is not None and row[8] is None:
                    status = "missing"
                elif row is not None and prior is not None:
                    status = "trading"
                elif row is not None:
                    status = "missing"
                elif day_status.get(instrument_id) == "suspended" and prior is not None:
                    status = "suspended"
                elif prior is None:
                    status = "not_eligible"
                else:
                    status = "missing"
                constituents.append(ConstituentInput(
                    symbol=str(member[1]), weight=float(member[2]),
                    current=(
                        DailyBar(
                            str(row[1]), _date_from_key(int(row[2])), float(row[3]),
                            float(row[4]), float(row[5]), float(row[6]), int(row[7]),
                        ) if row is not None else None
                    ),
                    previous_close=prior[0] if prior else None,
                    current_factor=float(row[8]) if row is not None else (prior[1] if prior else None),
                    previous_factor=prior[1] if prior else None,
                    status=status,
                ))
            item = calculate_bar(_date_from_key(trade_key), previous_index_close, constituents)
            for instrument_id, row in today.items():
                if row[8] is not None:
                    previous[instrument_id] = (float(row[6]), float(row[8]))
            if item is not None:
                output.append(item)
                previous_index_close = item.close
        return output

    def _calculate_custom_index_incremental(self, index_id: str):
        with self._lock:
            latest = self._connection.execute(
                """
                SELECT trade_date, close FROM custom_index_daily_bars
                WHERE index_id = ? ORDER BY trade_date DESC LIMIT 1
                """,
                (index_id,),
            ).fetchone()
            if latest is None:
                return self._calculate_custom_index_history(index_id)
            completed_through = self._connection.execute(
                """
                SELECT max(bar.trade_date)
                FROM daily_bars AS bar
                WHERE bar.instrument_id IN (
                    SELECT DISTINCT member.instrument_id
                    FROM custom_index_revision_members AS member
                    JOIN custom_index_revisions AS revision USING (revision_id)
                    WHERE revision.index_id = ?
                )
                """,
                (index_id,),
            ).fetchone()[0]
            if completed_through is None:
                return []
            dates = self._connection.execute(
                """
                SELECT trade_date FROM (
                    SELECT trade_date FROM trading_calendar
                    WHERE trade_date > ? AND trade_date <= ?
                    UNION
                    SELECT DISTINCT bar.trade_date
                    FROM daily_bars AS bar
                    WHERE bar.instrument_id IN (
                        SELECT DISTINCT member.instrument_id
                        FROM custom_index_revision_members AS member
                        JOIN custom_index_revisions AS revision USING (revision_id)
                        WHERE revision.index_id = ?
                    )
                      AND bar.trade_date > ? AND bar.trade_date <= ?
                )
                ORDER BY trade_date
                """,
                (
                    int(latest[0]), int(completed_through), index_id,
                    int(latest[0]), int(completed_through),
                ),
            ).fetchall()
        previous_index_close = float(latest[1])
        output = []
        for date_row in dates:
            trade_key = int(date_row[0])
            with self._lock:
                revision = self._connection.execute(
                    """
                    SELECT revision_id FROM custom_index_revisions
                    WHERE index_id = ? AND effective_from <= ?
                    ORDER BY effective_from DESC, revision_number DESC LIMIT 1
                    """,
                    (index_id, trade_key),
                ).fetchone()
                if revision is None:
                    continue
                members = self._connection.execute(
                    """
                    SELECT member.instrument_id, instrument.symbol, member.normalized_weight,
                           (SELECT min(listed_on) FROM instrument_catalog_entries
                            WHERE instrument_id = member.instrument_id),
                           (SELECT max(delisted_on) FROM instrument_catalog_entries
                            WHERE instrument_id = member.instrument_id)
                    FROM custom_index_revision_members AS member
                    JOIN instruments AS instrument USING (instrument_id)
                    WHERE member.revision_id = ? ORDER BY member.position
                    """,
                    (int(revision[0]),),
                ).fetchall()
                constituents = []
                for member in members:
                    current = self._connection.execute(
                        """
                        SELECT bar.open, bar.high, bar.low, bar.close, bar.volume, factor.factor
                        FROM daily_bars AS bar
                        LEFT JOIN stock_adjustment_factors AS factor
                          ON factor.instrument_id = bar.instrument_id
                         AND factor.trade_date = bar.trade_date
                        WHERE bar.instrument_id = ? AND bar.trade_date = ?
                        """,
                        (int(member[0]), trade_key),
                    ).fetchone()
                    prior = self._connection.execute(
                        """
                        SELECT bar.close, factor.factor
                        FROM daily_bars AS bar
                        LEFT JOIN stock_adjustment_factors AS factor
                          ON factor.instrument_id = bar.instrument_id
                         AND factor.trade_date = bar.trade_date
                        WHERE bar.instrument_id = ? AND bar.trade_date < ?
                        ORDER BY bar.trade_date DESC LIMIT 1
                        """,
                        (int(member[0]), trade_key),
                    ).fetchone()
                    explicit_status = self._connection.execute(
                        """
                        SELECT status FROM stock_trade_status
                        WHERE instrument_id = ? AND trade_date = ?
                        """,
                        (int(member[0]), trade_key),
                    ).fetchone()
                    listed_on = int(member[3]) if member[3] is not None else None
                    delisted_on = int(member[4]) if member[4] is not None else None
                    if (
                        (listed_on is not None and trade_key < listed_on)
                        or (delisted_on is not None and trade_key > delisted_on)
                    ):
                        status = "not_eligible"
                    elif current is not None and current[5] is None:
                        status = "missing"
                    elif current is not None and prior is not None and prior[1] is not None:
                        status = "trading"
                    elif current is not None:
                        status = "missing"
                    elif (
                        explicit_status is not None
                        and str(explicit_status[0]) == "suspended"
                        and prior is not None
                    ):
                        status = "suspended"
                    elif prior is not None:
                        status = "missing"
                    else:
                        status = "not_eligible"
                    constituents.append(ConstituentInput(
                        symbol=str(member[1]),
                        weight=float(member[2]),
                        current=(
                            DailyBar(
                                str(member[1]), _date_from_key(trade_key),
                                float(current[0]), float(current[1]), float(current[2]),
                                float(current[3]), int(current[4]),
                            ) if current is not None else None
                        ),
                        previous_close=float(prior[0]) if prior is not None else None,
                        current_factor=(
                            float(current[5]) if current is not None and current[5] is not None
                            else float(prior[1]) if prior is not None and prior[1] is not None
                            else None
                        ),
                        previous_factor=(
                            float(prior[1]) if prior is not None and prior[1] is not None else None
                        ),
                        status=status,
                    ))
            item = calculate_bar(
                _date_from_key(trade_key), previous_index_close, constituents
            )
            if item is not None:
                output.append(item)
                previous_index_close = item.close
        return output

    def _refresh_custom_index_snapshot_locked(self, index_id: str, now_ms: int) -> None:
        rows = self._connection.execute(
            """
            SELECT bar.trade_date, bar.close, custom.instrument_id
            FROM custom_index_daily_bars AS bar
            JOIN custom_indices AS custom USING (index_id)
            WHERE bar.index_id = ? ORDER BY bar.trade_date DESC LIMIT 2
            """,
            (index_id,),
        ).fetchall()
        if len(rows) < 2 or float(rows[1][1]) == 0:
            return
        source_id = self._source_id("local_custom_index")
        self._connection.execute(
            """
            INSERT INTO market_snapshots(
                instrument_id, trade_date, change_percent, total_market_cap,
                close, volume, amount, source_id, updated_at_ms
            ) VALUES (?, ?, ?, NULL, ?, NULL, NULL, ?, ?)
            ON CONFLICT(instrument_id, trade_date) DO UPDATE SET
                change_percent = excluded.change_percent, close = excluded.close,
                source_id = excluded.source_id, updated_at_ms = excluded.updated_at_ms
            """,
            (
                int(rows[0][2]), int(rows[0][0]),
                (float(rows[0][1]) / float(rows[1][1]) - 1.0) * 100.0,
                float(rows[0][1]), source_id, now_ms,
            ),
        )

    @staticmethod
    def _custom_index_summary(row: sqlite3.Row) -> dict[str, object]:
        return {
            "id": str(row[0]), "symbol": str(row[1]), "name": str(row[2]),
            "description": str(row[3]), "base_date": _date_from_key(int(row[4])),
            "base_value": float(row[5]), "status": str(row[6]), "last_error": row[7],
            "updated_at_ms": int(row[8]), "member_count": int(row[9]),
            "first_trade_date": _date_from_key(int(row[10])) if row[10] is not None else None,
            "last_trade_date": _date_from_key(int(row[11])) if row[11] is not None else None,
            "rows": int(row[12]),
            "quality_warning_days": int(row[13]),
        }
