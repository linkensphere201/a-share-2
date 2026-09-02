"""SQLite persistence for immutable screener runs and candidate snapshots."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timezone
import json
from uuid import uuid4

from stock_harness.sqlite_mapping import _date_from_key, _date_key


class SQLiteScreenerStoreMixin:
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

    def list_active_stock_symbols_for_screening(self) -> list[dict[str, str]]:
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
