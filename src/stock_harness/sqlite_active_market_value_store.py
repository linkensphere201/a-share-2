"""Point-in-time features and materialized active-market-value daily bars."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone

from stock_harness.active_market_value import (
    ALGORITHM_VERSION,
    DEFAULT_DEFINITION_ID,
    DEFAULT_NAME,
    DEFAULT_SYMBOL,
    ActiveMarketValueCalculator,
    ActiveMarketValueInput,
    normalize_bars,
)
from stock_harness.models import ActiveMarketValueFeature, InstrumentKind
from stock_harness.sqlite_mapping import _date_from_key, _date_key


class SQLiteActiveMarketValueStoreMixin:
    def upsert_active_market_value_features(
        self, source: str, trade_date: date,
        features: Sequence[ActiveMarketValueFeature],
    ) -> dict[str, int | str]:
        for item in features:
            item.validate()
            if item.trade_date != trade_date:
                raise ValueError("active-market-value feature date mismatch")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        symbols = {item.symbol.upper() for item in features}
        with self._lock, self._transaction():
            source_id = self._source_id(source)
            instrument_ids = self._instrument_ids(symbols)
            valid = [item for item in features if item.symbol.upper() in instrument_ids]
            self._connection.executemany(
                """
                INSERT INTO active_market_value_features(
                    instrument_id, trade_date, turnover_rate_f, free_share,
                    circ_market_value, total_market_value, close, source_id, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id, trade_date) DO UPDATE SET
                    turnover_rate_f = excluded.turnover_rate_f,
                    free_share = excluded.free_share,
                    circ_market_value = excluded.circ_market_value,
                    total_market_value = excluded.total_market_value,
                    close = excluded.close, source_id = excluded.source_id,
                    updated_at_ms = excluded.updated_at_ms
                """,
                ((
                    instrument_ids[item.symbol.upper()], _date_key(trade_date),
                    item.turnover_rate_f, item.free_share, item.circ_market_value,
                    item.total_market_value, item.close, source_id, now_ms,
                ) for item in valid),
            )
            skipped = len(features) - len(valid)
            self._connection.execute(
                """
                INSERT INTO active_market_value_feature_receipts(
                    source_id, trade_date, row_count, skipped_count, status, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, trade_date) DO UPDATE SET
                    row_count = excluded.row_count, skipped_count = excluded.skipped_count,
                    status = excluded.status, updated_at_ms = excluded.updated_at_ms
                """,
                (
                    source_id, _date_key(trade_date), len(valid), skipped,
                    "complete" if valid else "empty", now_ms,
                ),
            )
        return {"trade_date": trade_date.isoformat(), "row_count": len(valid), "skipped_count": skipped}

    def has_active_market_value_feature_receipt(self, source: str, trade_date: date) -> bool:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1 FROM active_market_value_feature_receipts AS receipt
                JOIN sources AS source USING (source_id)
                WHERE source.code = ? AND receipt.trade_date = ? AND receipt.status = 'complete'
                """,
                (source, _date_key(trade_date)),
            ).fetchone()
        return row is not None

    def active_market_value_feature_coverage(self) -> dict[str, object]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT min(trade_date), max(trade_date), count(DISTINCT trade_date), count(*)
                FROM active_market_value_features
                """
            ).fetchone()
        return {
            "first_trade_date": _date_from_key(int(row[0])) if row[0] is not None else None,
            "last_trade_date": _date_from_key(int(row[1])) if row[1] is not None else None,
            "trading_days": int(row[2]), "rows": int(row[3]),
        }

    def build_active_market_value_index(
        self, start_date: date | None = None, end_date: date | None = None,
        *, smoothing_period: int = 13, scale_k: float = 100,
        turnover_cap: float = 1, base_value: float = 1000,
        mode: str = "backfill",
    ) -> dict[str, object]:
        if mode not in {"backfill", "incremental", "correction"}:
            raise ValueError("invalid active-market-value build mode")
        initial_state: dict[int, float] = {}
        existing_base_close: float | None = None
        latest: int | None = None
        dirty_from: int | None = None
        dirty_through: int | None = None
        if mode in {"incremental", "correction"}:
            with self._lock:
                definition = self._connection.execute(
                    """
                    SELECT smoothing_period, scale_k, turnover_cap, base_value, base_date
                    FROM active_market_value_definitions WHERE definition_id = ?
                    """,
                    (DEFAULT_DEFINITION_ID,),
                ).fetchone()
                latest = self._connection.execute(
                    "SELECT max(trade_date) FROM active_market_value_daily_bars WHERE definition_id = ?",
                    (DEFAULT_DEFINITION_ID,),
                ).fetchone()[0]
                states = self._connection.execute(
                    "SELECT instrument_id, smoothed_turnover FROM active_market_value_stock_states WHERE definition_id = ?",
                    (DEFAULT_DEFINITION_ID,),
                ).fetchall()
                base_row = self._connection.execute(
                    "SELECT absolute_close FROM active_market_value_daily_bars WHERE definition_id = ? ORDER BY trade_date LIMIT 1",
                    (DEFAULT_DEFINITION_ID,),
                ).fetchone()
                dirty = self._connection.execute(
                    "SELECT dirty_from, dirty_through FROM active_market_value_dirty_ranges WHERE definition_id = ?",
                    (DEFAULT_DEFINITION_ID,),
                ).fetchone()
            if dirty is not None:
                dirty_from, dirty_through = int(dirty[0]), int(dirty[1])
            if definition is not None and any((
                int(definition[0]) != smoothing_period,
                float(definition[1]) != scale_k,
                float(definition[2]) != turnover_cap,
                float(definition[3]) != base_value,
            )):
                raise ValueError("incremental active-market-value parameters must match the materialized definition")
            if definition is None or latest is None or not states or base_row is None:
                mode = "backfill"
            else:
                existing_base_close = float(base_row[0])
                if dirty_from is not None and dirty_from <= latest:
                    mode = "correction"
                if mode == "correction":
                    base_date = int(definition[4]) if definition[4] is not None else None
                    if dirty_from is None:
                        dirty_from = _date_key(start_date) if start_date else None
                    if dirty_from is None:
                        raise ValueError("correction build requires a dirty range or start_date")
                    if base_date is None or dirty_from <= base_date:
                        mode = "backfill"
                if mode == "incremental":
                    initial_state = {int(row[0]): float(row[1]) for row in states}
        if mode == "backfill":
            initial_state = {}
            existing_base_close = None
        started_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            self._connection.execute(
                """
                INSERT INTO instruments(symbol, name, kind, exchange, active)
                VALUES (?, ?, ?, 'LOCAL', 1)
                ON CONFLICT(symbol) DO UPDATE SET name = excluded.name, active = 1
                """,
                (DEFAULT_SYMBOL, DEFAULT_NAME, InstrumentKind.INDEX.value),
            )
            instrument_id = int(self._connection.execute(
                "SELECT instrument_id FROM instruments WHERE symbol = ?", (DEFAULT_SYMBOL,)
            ).fetchone()[0])
            self._connection.execute(
                """
                INSERT INTO active_market_value_definitions(
                    definition_id, instrument_id, algorithm_version, smoothing_period,
                    scale_k, turnover_cap, base_value, base_date, status,
                    created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'building', ?, ?)
                ON CONFLICT(definition_id) DO UPDATE SET
                    algorithm_version = excluded.algorithm_version,
                    smoothing_period = excluded.smoothing_period,
                    scale_k = excluded.scale_k, turnover_cap = excluded.turnover_cap,
                    base_value = excluded.base_value, status = 'building',
                    last_error = NULL, updated_at_ms = excluded.updated_at_ms
                """,
                (
                    DEFAULT_DEFINITION_ID, instrument_id, ALGORITHM_VERSION,
                    smoothing_period, scale_k, turnover_cap, base_value,
                    started_ms, started_ms,
                ),
            )
            run_id = int(self._connection.execute(
                """
                INSERT INTO active_market_value_build_runs(
                    definition_id, mode, row_count, status, message, started_at_ms
                ) VALUES (?, ?, 0, 'running', '', ?) RETURNING run_id
                """,
                (DEFAULT_DEFINITION_ID, mode, started_ms),
            ).fetchone()[0])

        start_key = _date_key(start_date) if start_date else 0
        emit_start_key = start_key
        end_key = _date_key(end_date) if end_date else 99_991_231
        if mode == "incremental":
            assert latest is not None
            start_key = max(start_key, _date_key(_date_from_key(latest) + timedelta(days=1)))
            emit_start_key = start_key
        elif mode == "correction":
            assert dirty_from is not None
            emit_start_key = dirty_from
            start_key = 0
            end_key = 99_991_231
        calculator = ActiveMarketValueCalculator(
            smoothing_period, scale_k, turnover_cap, initial_state
        )
        absolute_rows: list[tuple[int, tuple[float, float, float, float, int, int, float]]] = []
        try:
            with self._lock:
                cursor = self._connection.execute(
                    """
                    WITH expected_counts AS (
                        SELECT expected.trade_date, count(*) AS expected_count
                        FROM daily_bars AS expected
                        JOIN instruments AS expected_instrument
                          ON expected_instrument.instrument_id = expected.instrument_id
                        WHERE expected_instrument.kind = 'stock'
                          AND expected.trade_date BETWEEN ? AND ?
                        GROUP BY expected.trade_date
                    )
                    SELECT feature.trade_date, feature.instrument_id,
                           feature.turnover_rate_f, feature.free_share, feature.close,
                           bar.open, bar.high, bar.low, bar.close,
                           expected_counts.expected_count
                    FROM active_market_value_features AS feature
                    JOIN instruments AS instrument USING (instrument_id)
                    LEFT JOIN daily_bars AS bar
                      ON bar.instrument_id = feature.instrument_id
                     AND bar.trade_date = feature.trade_date
                    LEFT JOIN expected_counts USING (trade_date)
                    WHERE instrument.kind = 'stock'
                      AND feature.trade_date BETWEEN ? AND ?
                    ORDER BY feature.trade_date, feature.instrument_id
                    """,
                    (start_key, end_key, start_key, end_key),
                )
                current_date: int | None = None
                current_expected_count: int | None = None
                current: list[ActiveMarketValueInput] = []
                for row in cursor:
                    trade_key = int(row[0])
                    if current_date is not None and trade_key != current_date:
                        calculated = calculator.calculate_absolute(
                            current_date, current, current_expected_count
                        )
                        if calculated is not None:
                            absolute_rows.append(calculated)
                        current = []
                    current_date = trade_key
                    current_expected_count = int(row[9]) if row[9] is not None else None
                    current.append(ActiveMarketValueInput(
                        instrument_id=int(row[1]), turnover_rate_f=float(row[2]),
                        free_share=float(row[3]), feature_close=float(row[4]),
                        open=float(row[5]) if row[5] is not None else None,
                        high=float(row[6]) if row[6] is not None else None,
                        low=float(row[7]) if row[7] is not None else None,
                        close=float(row[8]) if row[8] is not None else None,
                    ))
                if current_date is not None:
                    calculated = calculator.calculate_absolute(
                        current_date, current, current_expected_count
                    )
                    if calculated is not None:
                        absolute_rows.append(calculated)
            bars = normalize_bars(absolute_rows, base_value, existing_base_close)
            if mode == "correction":
                bars = [item for item in bars if item.trade_date >= emit_start_key]
            completed_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            with self._lock, self._transaction():
                source_id = self._source_id(
                    "stock_harness_amv", acquired_via="derived", source_system="stock_harness"
                )
                if mode == "backfill":
                    self._connection.execute(
                        "DELETE FROM active_market_value_daily_bars WHERE definition_id = ?",
                        (DEFAULT_DEFINITION_ID,),
                    )
                    self._connection.execute(
                        "DELETE FROM daily_bars WHERE instrument_id = ?", (instrument_id,)
                    )
                elif mode == "correction":
                    self._connection.execute(
                        "DELETE FROM active_market_value_daily_bars WHERE definition_id = ? AND trade_date >= ?",
                        (DEFAULT_DEFINITION_ID, emit_start_key),
                    )
                    self._connection.execute(
                        "DELETE FROM daily_bars WHERE instrument_id = ? AND trade_date >= ?",
                        (instrument_id, emit_start_key),
                    )
                self._connection.executemany(
                    """
                    INSERT INTO active_market_value_daily_bars(
                        definition_id, trade_date, absolute_open, absolute_high,
                        absolute_low, absolute_close, open, high, low, close,
                        eligible_count, total_count, coverage_ratio,
                        algorithm_version, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(definition_id, trade_date) DO UPDATE SET
                        absolute_open = excluded.absolute_open,
                        absolute_high = excluded.absolute_high,
                        absolute_low = excluded.absolute_low,
                        absolute_close = excluded.absolute_close,
                        open = excluded.open, high = excluded.high,
                        low = excluded.low, close = excluded.close,
                        eligible_count = excluded.eligible_count,
                        total_count = excluded.total_count,
                        coverage_ratio = excluded.coverage_ratio,
                        algorithm_version = excluded.algorithm_version,
                        updated_at_ms = excluded.updated_at_ms
                    """,
                    ((
                        DEFAULT_DEFINITION_ID, item.trade_date, item.absolute_open,
                        item.absolute_high, item.absolute_low, item.absolute_close,
                        item.open, item.high, item.low, item.close,
                        item.eligible_count, item.total_count, item.coverage_ratio,
                        ALGORITHM_VERSION, completed_ms,
                    ) for item in bars),
                )
                self._connection.executemany(
                    """
                    INSERT INTO daily_bars(
                        instrument_id, trade_date, open, high, low, close,
                        volume, source_id, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                    ON CONFLICT(instrument_id, trade_date) DO UPDATE SET
                        open = excluded.open, high = excluded.high,
                        low = excluded.low, close = excluded.close,
                        volume = 0, source_id = excluded.source_id,
                        updated_at_ms = excluded.updated_at_ms
                    """,
                    ((
                        instrument_id, item.trade_date, item.open, item.high,
                        item.low, item.close, source_id, completed_ms,
                    ) for item in bars),
                )
                if mode in {"incremental", "correction"}:
                    self._connection.execute(
                        """
                        UPDATE active_market_value_definitions
                        SET status = 'ready', updated_at_ms = ? WHERE definition_id = ?
                        """,
                        (completed_ms, DEFAULT_DEFINITION_ID),
                    )
                else:
                    base_date = bars[0].trade_date if bars else None
                    self._connection.execute(
                        """
                        UPDATE active_market_value_definitions
                        SET base_date = ?, status = 'ready', updated_at_ms = ?
                        WHERE definition_id = ?
                        """,
                        (base_date, completed_ms, DEFAULT_DEFINITION_ID),
                    )
                if mode != "incremental":
                    self._connection.execute(
                        "DELETE FROM active_market_value_stock_states WHERE definition_id = ?",
                        (DEFAULT_DEFINITION_ID,),
                    )
                state_date = bars[-1].trade_date if bars else None
                self._connection.executemany(
                    """
                    INSERT INTO active_market_value_stock_states(
                        definition_id, instrument_id, as_of_date,
                        smoothed_turnover, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(definition_id, instrument_id) DO UPDATE SET
                        as_of_date = excluded.as_of_date,
                        smoothed_turnover = excluded.smoothed_turnover,
                        updated_at_ms = excluded.updated_at_ms
                    """,
                    ((
                        DEFAULT_DEFINITION_ID, instrument_id_value, state_date,
                        smoothed, completed_ms,
                    ) for instrument_id_value, smoothed in calculator.state.items()) if state_date else (),
                )
                self._connection.execute(
                    """
                    UPDATE active_market_value_build_runs
                    SET from_date = ?, through_date = ?, row_count = ?, status = 'completed',
                        message = 'materialized', completed_at_ms = ? WHERE run_id = ?
                    """,
                    (
                        bars[0].trade_date if bars else None,
                        bars[-1].trade_date if bars else None,
                        len(bars), completed_ms, run_id,
                    ),
                )
                if state_date is not None:
                    self._connection.execute(
                        """
                        DELETE FROM active_market_value_dirty_ranges
                        WHERE definition_id = ? AND dirty_through <= ? AND updated_at_ms <= ?
                        """,
                        (DEFAULT_DEFINITION_ID, state_date, started_ms),
                    )
            return self.get_active_market_value_index()
        except Exception as exc:
            failed_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            with self._lock, self._transaction():
                self._connection.execute(
                    "UPDATE active_market_value_definitions SET status = 'error', last_error = ?, updated_at_ms = ? WHERE definition_id = ?",
                    (str(exc)[:500], failed_ms, DEFAULT_DEFINITION_ID),
                )
                self._connection.execute(
                    "UPDATE active_market_value_build_runs SET status = 'failed', message = ?, completed_at_ms = ? WHERE run_id = ?",
                    (str(exc)[:500], failed_ms, run_id),
                )
            raise

    def get_active_market_value_index(self) -> dict[str, object]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT definition.definition_id, instrument.symbol, instrument.name,
                       definition.algorithm_version, definition.smoothing_period,
                       definition.scale_k, definition.turnover_cap, definition.base_value,
                       definition.base_date, definition.status, definition.last_error,
                       min(bar.trade_date), max(bar.trade_date), count(bar.trade_date),
                       min(bar.coverage_ratio),
                       (SELECT latest.absolute_close
                        FROM active_market_value_daily_bars AS latest
                        WHERE latest.definition_id = definition.definition_id
                        ORDER BY latest.trade_date DESC LIMIT 1),
                       (SELECT latest.coverage_ratio
                        FROM active_market_value_daily_bars AS latest
                        WHERE latest.definition_id = definition.definition_id
                        ORDER BY latest.trade_date DESC LIMIT 1),
                       (SELECT latest.close
                        FROM active_market_value_daily_bars AS latest
                        WHERE latest.definition_id = definition.definition_id
                        ORDER BY latest.trade_date DESC LIMIT 1),
                       (SELECT previous.close
                        FROM active_market_value_daily_bars AS previous
                        WHERE previous.definition_id = definition.definition_id
                        ORDER BY previous.trade_date DESC LIMIT 1 OFFSET 1),
                       (SELECT dirty.dirty_from
                        FROM active_market_value_dirty_ranges AS dirty
                        WHERE dirty.definition_id = definition.definition_id),
                       (SELECT dirty.dirty_through
                        FROM active_market_value_dirty_ranges AS dirty
                        WHERE dirty.definition_id = definition.definition_id),
                       (SELECT dirty.reason
                        FROM active_market_value_dirty_ranges AS dirty
                        WHERE dirty.definition_id = definition.definition_id)
                FROM active_market_value_definitions AS definition
                JOIN instruments AS instrument USING (instrument_id)
                LEFT JOIN active_market_value_daily_bars AS bar USING (definition_id)
                WHERE definition.definition_id = ?
                GROUP BY definition.definition_id
                """,
                (DEFAULT_DEFINITION_ID,),
            ).fetchone()
        if row is None:
            return {"status": "missing", "symbol": DEFAULT_SYMBOL, "rows": 0}
        latest_close = float(row[17]) if row[17] is not None else None
        previous_close = float(row[18]) if row[18] is not None else None
        return {
            "id": str(row[0]), "symbol": str(row[1]), "name": str(row[2]),
            "algorithm_version": str(row[3]), "smoothing_period": int(row[4]),
            "scale_k": float(row[5]), "turnover_cap": float(row[6]),
            "base_value": float(row[7]),
            "base_date": _date_from_key(int(row[8])) if row[8] is not None else None,
            "status": str(row[9]), "last_error": row[10],
            "first_trade_date": _date_from_key(int(row[11])) if row[11] is not None else None,
            "last_trade_date": _date_from_key(int(row[12])) if row[12] is not None else None,
            "rows": int(row[13]),
            "minimum_coverage_ratio": float(row[14]) if row[14] is not None else None,
            "latest_absolute_close": float(row[15]) if row[15] is not None else None,
            "latest_coverage_ratio": float(row[16]) if row[16] is not None else None,
            "latest_change_percent": (
                (latest_close / previous_close - 1) * 100
                if latest_close is not None and previous_close not in (None, 0) else None
            ),
            "dirty_from_date": _date_from_key(int(row[19])) if row[19] is not None else None,
            "dirty_through_date": _date_from_key(int(row[20])) if row[20] is not None else None,
            "dirty_reason": str(row[21]) if row[21] is not None else None,
        }

    def list_active_market_value_diagnostics(
        self, start_date: date, end_date: date
    ) -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT trade_date, absolute_open, absolute_high, absolute_low,
                       absolute_close, open, high, low, close, eligible_count,
                       total_count, coverage_ratio, algorithm_version
                FROM active_market_value_daily_bars
                WHERE definition_id = ? AND trade_date BETWEEN ? AND ?
                ORDER BY trade_date
                """,
                (DEFAULT_DEFINITION_ID, _date_key(start_date), _date_key(end_date)),
            ).fetchall()
        return [{
            "trade_date": _date_from_key(int(row[0])),
            "absolute_open": float(row[1]), "absolute_high": float(row[2]),
            "absolute_low": float(row[3]), "absolute_close": float(row[4]),
            "open": float(row[5]), "high": float(row[6]), "low": float(row[7]),
            "close": float(row[8]), "eligible_count": int(row[9]),
            "total_count": int(row[10]), "coverage_ratio": float(row[11]),
            "algorithm_version": str(row[12]),
        } for row in rows]
