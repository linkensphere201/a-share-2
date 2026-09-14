"""SQLite persistence for immutable screener runs and candidate snapshots."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timezone
import json
from uuid import uuid4

from stock_harness.sqlite_mapping import _date_from_key, _date_key


class SQLiteScreenerStoreMixin:
    def _ensure_screener_candidate_states(self) -> None:
        """Extend persisted screener states while retaining immutable old runs."""
        with self._lock, self._writer_lock:
            row = self._connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' "
                "AND name = 'screener_candidates'"
            ).fetchone()
            if row is None or "'accumulating'" in str(row[0]):
                return
            self._connection.execute("PRAGMA foreign_keys = OFF")
            try:
                self._connection.executescript(
                    """
                    BEGIN IMMEDIATE;
                    CREATE TABLE screener_candidates_v2 (
                        run_id TEXT NOT NULL,
                        rank INTEGER NOT NULL CHECK (rank > 0),
                        instrument_id INTEGER NOT NULL,
                        state TEXT NOT NULL CHECK (
                            state IN (
                                'critical-breakout', 'breakout-retest',
                                'broken-out', 'accumulating'
                            )
                        ),
                        score REAL NOT NULL,
                        line_item_id TEXT NOT NULL,
                        line_code TEXT NOT NULL,
                        analysis_run_id TEXT NOT NULL,
                        evidence_json TEXT NOT NULL,
                        PRIMARY KEY (run_id, instrument_id),
                        UNIQUE (run_id, rank),
                        FOREIGN KEY (run_id) REFERENCES screener_runs(run_id) ON DELETE CASCADE,
                        FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id),
                        FOREIGN KEY (analysis_run_id) REFERENCES generated_analysis_runs(run_id)
                    ) WITHOUT ROWID;
                    INSERT INTO screener_candidates_v2
                    SELECT * FROM screener_candidates;
                    DROP TABLE screener_candidates;
                    ALTER TABLE screener_candidates_v2 RENAME TO screener_candidates;
                    CREATE INDEX screener_candidates_rank
                    ON screener_candidates(run_id, rank);
                    COMMIT;
                    """
                )
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
            finally:
                self._connection.execute("PRAGMA foreign_keys = ON")

    def create_screener_run(
        self,
        strategy_id: str,
        strategy_version: str,
        as_of_date: date,
        parameters: dict[str, object],
    ) -> dict[str, object]:
        run_id = str(uuid4())
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            self._connection.execute(
                """
                INSERT INTO screener_runs(
                    run_id, strategy_id, strategy_version, as_of_date,
                    parameters_json, status, started_at_ms
                ) VALUES (?, ?, ?, ?, ?, 'running', ?)
                """,
                (
                    run_id, strategy_id, strategy_version, _date_key(as_of_date),
                    json.dumps(parameters, ensure_ascii=False, sort_keys=True), now_ms,
                ),
            )
        return self.get_screener_run(run_id)  # type: ignore[return-value]

    def record_screener_exclusions(
        self,
        strategy_id: str,
        events: Sequence[dict[str, object]],
        *,
        limit: int = 100,
        through_date: date | None = None,
    ) -> list[dict[str, object]]:
        if limit < 1:
            raise ValueError("screener exclusion-pool limit must be positive")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            state = self._connection.execute(
                """
                SELECT processed_through_date FROM screener_exclusion_sync_state
                WHERE strategy_id = ?
                """,
                (strategy_id,),
            ).fetchone()
            processed_key = int(state[0]) if state is not None else None
            if processed_key is None:
                existing = self._connection.execute(
                    """
                    SELECT max(event_date) FROM screener_exclusion_pool
                    WHERE strategy_id = ?
                    """,
                    (strategy_id,),
                ).fetchone()
                if existing is not None and existing[0] is not None:
                    processed_key = int(existing[0])
            for event in events:
                identity = self._canonical_instrument_identity(str(event["symbol"]))
                if identity is None:
                    continue
                _, instrument_id = identity
                event_date = event["event_date"]
                if not isinstance(event_date, date):
                    raise ValueError("screener exclusion event_date must be a date")
                if processed_key is not None and _date_key(event_date) <= processed_key:
                    continue
                existing = self._connection.execute(
                    """
                    SELECT event_date FROM screener_exclusion_pool
                    WHERE strategy_id = ? AND instrument_id = ?
                    """,
                    (strategy_id, instrument_id),
                ).fetchone()
                if existing is not None and int(existing[0]) >= _date_key(event_date):
                    continue
                self._connection.execute(
                    """
                    INSERT INTO screener_exclusion_pool(
                        strategy_id, instrument_id, event_date, reason_code,
                        reason_text, evidence_json, entered_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(strategy_id, instrument_id) DO UPDATE SET
                        event_date = excluded.event_date,
                        reason_code = excluded.reason_code,
                        reason_text = excluded.reason_text,
                        evidence_json = excluded.evidence_json,
                        entered_at_ms = excluded.entered_at_ms
                    """,
                    (
                        strategy_id, instrument_id, _date_key(event_date),
                        str(event["reason_code"]), str(event["reason_text"]),
                        json.dumps(event.get("evidence", {}), ensure_ascii=False, sort_keys=True),
                        now_ms,
                    ),
                )
            stale = self._connection.execute(
                """
                SELECT entry_id FROM screener_exclusion_pool
                WHERE strategy_id = ?
                ORDER BY event_date DESC, entered_at_ms DESC, entry_id DESC
                LIMIT -1 OFFSET ?
                """,
                (strategy_id, limit),
            ).fetchall()
            if stale:
                placeholders = ",".join("?" for _ in stale)
                self._connection.execute(
                    f"DELETE FROM screener_exclusion_pool WHERE entry_id IN ({placeholders})",
                    tuple(int(row[0]) for row in stale),
                )
            effective_through = through_date
            if effective_through is None:
                effective_through = max(
                    (
                        value for value in (event.get("event_date") for event in events)
                        if isinstance(value, date)
                    ),
                    default=None,
                )
            if effective_through is not None and (
                processed_key is None or _date_key(effective_through) > processed_key
            ):
                self._connection.execute(
                    """
                    INSERT INTO screener_exclusion_sync_state(
                        strategy_id, processed_through_date, updated_at_ms
                    ) VALUES (?, ?, ?)
                    ON CONFLICT(strategy_id) DO UPDATE SET
                        processed_through_date = excluded.processed_through_date,
                        updated_at_ms = excluded.updated_at_ms
                    """,
                    (strategy_id, _date_key(effective_through), now_ms),
                )
        return self.list_screener_exclusion_pool(strategy_id, limit=limit)

    def list_screener_exclusion_pool(
        self, strategy_id: str, *, limit: int = 100,
    ) -> list[dict[str, object]]:
        if not 1 <= limit <= 100:
            raise ValueError("screener exclusion-pool limit must be between 1 and 100")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT pool.entry_id, instrument.symbol, instrument.name,
                       instrument.exchange, pool.event_date, pool.reason_code,
                       pool.reason_text, pool.evidence_json, pool.entered_at_ms
                FROM screener_exclusion_pool AS pool
                JOIN instruments AS instrument USING (instrument_id)
                WHERE pool.strategy_id = ?
                ORDER BY pool.event_date DESC, pool.entered_at_ms DESC, pool.entry_id DESC
                LIMIT ?
                """,
                (strategy_id, limit),
            ).fetchall()
        return [{
            "entry_id": int(row[0]), "strategy_id": strategy_id,
            "symbol": str(row[1]), "name": str(row[2]),
            "exchange": str(row[3]), "kind": "stock",
            "event_date": _date_from_key(int(row[4])),
            "reason_code": str(row[5]), "reason_text": str(row[6]),
            "evidence": json.loads(str(row[7])), "entered_at_ms": int(row[8]),
        } for row in rows]

    def update_screener_progress(
        self, run_id: str, *, universe_count: int, scanned_count: int
    ) -> None:
        with self._lock, self._transaction():
            cursor = self._connection.execute(
                """
                UPDATE screener_runs
                SET universe_count = ?, scanned_count = ?
                WHERE run_id = ? AND status = 'running'
                """,
                (universe_count, scanned_count, run_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("screener run is not running")

    def complete_screener_run(
        self, run_id: str, candidates: Sequence[dict[str, object]], retention: int = 10
    ) -> dict[str, object]:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        if retention < 1:
            raise ValueError("screener retention must be positive")
        with self._lock, self._transaction():
            status = self._connection.execute(
                "SELECT status FROM screener_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if status is None or str(status[0]) != "running":
                raise ValueError("screener run is not running")
            for rank, candidate in enumerate(candidates, 1):
                identity = self._canonical_instrument_identity(str(candidate["symbol"]))
                if identity is None:
                    raise ValueError(f"unknown screener candidate: {candidate['symbol']}")
                _, instrument_id = identity
                self._connection.execute(
                    """
                    INSERT INTO screener_candidates(
                        run_id, rank, instrument_id, state, score, line_item_id,
                        line_code, analysis_run_id, evidence_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id, rank, instrument_id, candidate["state"],
                        float(candidate["score"]), candidate["line_item_id"],
                        candidate["line_code"], candidate["analysis_run_id"],
                        json.dumps(candidate["evidence"], ensure_ascii=False, sort_keys=True),
                    ),
                )
            self._connection.execute(
                """
                UPDATE screener_runs
                SET status = 'succeeded', scanned_count = universe_count,
                    candidate_count = ?, completed_at_ms = ?
                WHERE run_id = ?
                """,
                (len(candidates), now_ms, run_id),
            )
            old_rows = self._connection.execute(
                """
                SELECT run_id FROM screener_runs
                WHERE status IN ('succeeded', 'failed') AND run_id <> ?
                ORDER BY started_at_ms DESC, run_id DESC
                LIMIT -1 OFFSET ?
                """,
                (run_id, max(retention - 1, 0)),
            ).fetchall()
            if old_rows:
                placeholders = ",".join("?" for _ in old_rows)
                self._connection.execute(
                    f"DELETE FROM screener_runs WHERE run_id IN ({placeholders})",
                    tuple(str(row[0]) for row in old_rows),
                )
        return self.get_screener_run(run_id)  # type: ignore[return-value]

    def fail_screener_run(self, run_id: str, error: str) -> None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            self._connection.execute(
                """
                UPDATE screener_runs
                SET status = 'failed', error = ?, completed_at_ms = ?
                WHERE run_id = ? AND status = 'running'
                """,
                (" ".join(error.split())[:2000], now_ms, run_id),
            )

    def recover_interrupted_screener_runs(self) -> int:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            cursor = self._connection.execute(
                """
                UPDATE screener_runs
                SET status = 'failed', error = 'application stopped during screening',
                    completed_at_ms = ?
                WHERE status = 'running'
                """,
                (now_ms,),
            )
            return cursor.rowcount

    def list_screener_runs(self, limit: int = 10) -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT run_id, strategy_id, strategy_version, as_of_date,
                       parameters_json, status, universe_count, scanned_count,
                       candidate_count, error, started_at_ms, completed_at_ms
                FROM screener_runs
                ORDER BY started_at_ms DESC, run_id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_run_row(row) for row in rows]

    def get_screener_run(self, run_id: str) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT run_id, strategy_id, strategy_version, as_of_date,
                       parameters_json, status, universe_count, scanned_count,
                       candidate_count, error, started_at_ms, completed_at_ms
                FROM screener_runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        return _run_row(row) if row else None

    def get_latest_succeeded_screener_run(
        self, on_or_before: date, strategy_id: str | None = None,
    ) -> dict[str, object] | None:
        strategy_clause = " AND strategy_id = ?" if strategy_id else ""
        parameters: tuple[object, ...] = (
            (_date_key(on_or_before), strategy_id)
            if strategy_id else (_date_key(on_or_before),)
        )
        with self._lock:
            row = self._connection.execute(
                f"""
                SELECT run_id, strategy_id, strategy_version, as_of_date,
                       parameters_json, status, universe_count, scanned_count,
                       candidate_count, error, started_at_ms, completed_at_ms
                FROM screener_runs
                WHERE status = 'succeeded' AND as_of_date <= ?{strategy_clause}
                ORDER BY as_of_date DESC, completed_at_ms DESC, run_id DESC
                LIMIT 1
                """, parameters,
            ).fetchone()
        return _run_row(row) if row else None

    def delete_screener_run(self, run_id: str) -> bool:
        with self._lock, self._transaction():
            row = self._connection.execute(
                "SELECT status FROM screener_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                return False
            if str(row[0]) == "running":
                raise ValueError("running screener run cannot be deleted")
            self._connection.execute(
                "DELETE FROM screener_runs WHERE run_id = ?", (run_id,)
            )
        return True

    def list_screener_candidates(self, run_id: str) -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT candidate.rank, instrument.symbol, instrument.name,
                       candidate.state, candidate.score, candidate.line_item_id,
                       candidate.line_code, candidate.analysis_run_id,
                       candidate.evidence_json, instrument.exchange
                FROM screener_candidates AS candidate
                JOIN instruments AS instrument USING (instrument_id)
                WHERE candidate.run_id = ? ORDER BY candidate.rank
                """,
                (run_id,),
            ).fetchall()
        return [{
            "rank": int(row[0]), "symbol": str(row[1]), "name": str(row[2]),
            "state": str(row[3]), "score": float(row[4]),
            "line_item_id": str(row[5]), "line_code": str(row[6]),
            "analysis_run_id": str(row[7]), "evidence": json.loads(str(row[8])),
            "exchange": str(row[9]), "kind": "stock",
        } for row in rows]

    def list_active_stock_symbols_for_screening(
        self, as_of_date: date | None = None,
    ) -> list[dict[str, str]]:
        if as_of_date is not None:
            with self._lock:
                rows = self._connection.execute(
                    """
                    SELECT instrument.symbol, instrument.name, instrument.exchange
                    FROM daily_bars AS bar
                    JOIN instruments AS instrument USING (instrument_id)
                    WHERE instrument.kind = 'stock' AND bar.trade_date = ?
                    ORDER BY instrument.symbol
                    """,
                    (_date_key(as_of_date),),
                ).fetchall()
            return [{
                "symbol": str(row[0]), "name": str(row[1]),
                "exchange": str(row[2]),
            } for row in rows]
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT symbol, name, exchange FROM instruments
                WHERE kind = 'stock' AND active = 1 ORDER BY symbol
                """
            ).fetchall()
        return [{"symbol": str(row[0]), "name": str(row[1]), "exchange": str(row[2])} for row in rows]

    def get_latest_stock_daily_bar_date(self) -> date | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT max(bar.trade_date) FROM daily_bars AS bar
                JOIN instruments AS instrument USING (instrument_id)
                WHERE instrument.kind = 'stock' AND instrument.active = 1
                """
            ).fetchone()
        return _date_from_key(int(row[0])) if row and row[0] is not None else None

    def list_stock_symbols_with_daily_bars(
        self, start_date: date, end_date: date,
    ) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT DISTINCT instrument.symbol
                FROM daily_bars AS bar
                JOIN instruments AS instrument USING (instrument_id)
                WHERE instrument.kind = 'stock'
                  AND bar.trade_date BETWEEN ? AND ?
                ORDER BY instrument.symbol
                """,
                (_date_key(start_date), _date_key(end_date)),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def list_stock_limit_up_dates(
        self, symbol: str, start_date: date, end_date: date,
    ) -> set[date]:
        """Read exact upper-limit touches, with board-aware return fallback."""
        identity = self._canonical_instrument_identity(symbol)
        if identity is None:
            return set()
        _, instrument_id = identity
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT current.trade_date, current.high, current.close,
                       previous.close, limits.up_limit
                FROM daily_bars AS current
                LEFT JOIN daily_bars AS previous
                  ON previous.instrument_id = current.instrument_id
                 AND previous.trade_date = (
                    SELECT max(prior.trade_date) FROM daily_bars AS prior
                    WHERE prior.instrument_id = current.instrument_id
                      AND prior.trade_date < current.trade_date
                 )
                LEFT JOIN stock_daily_limits AS limits
                  ON limits.instrument_id = current.instrument_id
                 AND limits.trade_date = current.trade_date
                WHERE current.instrument_id = ?
                  AND current.trade_date BETWEEN ? AND ?
                ORDER BY current.trade_date
                """,
                (instrument_id, _date_key(start_date), _date_key(end_date)),
            ).fetchall()
        threshold = 1.295 if symbol.startswith(("8", "4", "92")) else (
            1.195 if symbol.startswith(("300", "301", "688", "689")) else 1.095
        )
        return {
            _date_from_key(int(row[0])) for row in rows
            if (
                row[4] is not None and float(row[1]) >= float(row[4]) - 0.005
            ) or (
                row[4] is None and row[3] is not None
                and float(row[1]) / float(row[3]) >= threshold
            )
        }


def _run_row(row) -> dict[str, object]:
    return {
        "run_id": str(row[0]), "strategy_id": str(row[1]),
        "strategy_version": str(row[2]), "as_of_date": _date_from_key(int(row[3])),
        "parameters": json.loads(str(row[4])), "status": str(row[5]),
        "universe_count": int(row[6]), "scanned_count": int(row[7]),
        "candidate_count": int(row[8]), "error": row[9],
        "started_at_ms": int(row[10]),
        "completed_at_ms": int(row[11]) if row[11] is not None else None,
    }
