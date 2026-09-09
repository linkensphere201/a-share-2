"""SQLite persistence for immutable signal-review snapshots."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timezone
import json
from uuid import uuid4

from stock_harness.sqlite_mapping import _date_from_key, _date_key


class SQLiteSignalReviewStoreMixin:
    def list_compatible_prior_signal_review_runs(
        self, run_id: str, limit: int = 7,
    ) -> list[dict[str, object]]:
        if not 1 <= limit <= 20:
            raise ValueError("prior signal run limit must be between 1 and 20")
        with self._lock:
            rows = self._connection.execute(
                """
                WITH current AS (
                    SELECT signal_id, definition_version, algorithm_version,
                           cadence, effective_date, revision, parameters_json
                    FROM signal_review_runs WHERE run_id = ?
                ), compatible AS (
                    SELECT prior.run_id, prior.effective_date, prior.revision,
                           row_number() OVER (
                               PARTITION BY prior.effective_date
                               ORDER BY prior.revision DESC
                           ) AS date_revision_rank
                    FROM signal_review_runs AS prior, current
                    WHERE prior.signal_id = current.signal_id
                      AND prior.definition_version = current.definition_version
                      AND prior.algorithm_version = current.algorithm_version
                      AND prior.cadence = current.cadence
                      AND prior.parameters_json = current.parameters_json
                      AND prior.status = 'succeeded'
                      AND (
                          prior.effective_date < current.effective_date OR
                          (prior.effective_date = current.effective_date
                           AND prior.revision < current.revision)
                      )
                )
                SELECT run_id FROM compatible
                WHERE date_revision_rank = 1
                ORDER BY effective_date DESC, revision DESC
                LIMIT ?
                """, (run_id, limit),
            ).fetchall()
        return [
            value for row in rows
            if (value := self.get_signal_review_run(str(row[0]))) is not None
        ]

    def create_signal_review_run(
        self, *, signal_id: str, definition_version: str, algorithm_version: str,
        cadence: str, effective_date: date, parameters: dict[str, object],
    ) -> dict[str, object]:
        run_id = str(uuid4())
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        parameters_json = json.dumps(parameters, ensure_ascii=False, sort_keys=True)
        with self._lock, self._transaction():
            prior = self._connection.execute(
                """
                SELECT run_id FROM signal_review_runs
                WHERE signal_id = ? AND definition_version = ?
                  AND algorithm_version = ? AND cadence = ?
                  AND parameters_json = ? AND status = 'succeeded'
                  AND effective_date <= ?
                ORDER BY effective_date DESC, revision DESC LIMIT 1
                """, (signal_id, definition_version, algorithm_version, cadence,
                      parameters_json, _date_key(effective_date)),
            ).fetchone()
            revision = int(self._connection.execute(
                "SELECT coalesce(max(revision), 0) + 1 FROM signal_review_runs "
                "WHERE signal_id = ? AND effective_date = ?",
                (signal_id, _date_key(effective_date)),
            ).fetchone()[0])
            self._connection.execute(
                """
                INSERT INTO signal_review_runs(
                    run_id, signal_id, definition_version, algorithm_version,
                    cadence, effective_date, revision, prior_run_id,
                    parameters_json, status, phase, started_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', 'queued', ?)
                """,
                (run_id, signal_id, definition_version, algorithm_version, cadence,
                 _date_key(effective_date), revision, str(prior[0]) if prior else None,
                 parameters_json, now_ms),
            )
        return self.get_signal_review_run(run_id)  # type: ignore[return-value]

    def update_signal_review_progress(
        self, run_id: str, *, phase: str, work_total: int, work_done: int,
    ) -> None:
        with self._lock, self._transaction():
            cursor = self._connection.execute(
                """
                UPDATE signal_review_runs SET phase = ?, work_total = ?, work_done = ?
                WHERE run_id = ? AND status = 'running'
                """, (phase, work_total, work_done, run_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("signal review run is not running")

    def complete_signal_review_run(
        self, run_id: str, *, items: Sequence[dict[str, object]],
        summary: dict[str, object], input_digest: str,
        scores: Sequence[dict[str, object]] = (),
    ) -> dict[str, object]:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        counts = {name: sum(item["change_type"] == name for item in items)
                  for name in ("added", "retained", "removed")}
        aliases = [
            str(evidence["alias"])
            for item in items for evidence in item.get("evidence", [])
        ]
        if not all(alias for alias in aliases) or len(aliases) != len(set(aliases)):
            raise ValueError("signal evidence aliases must be non-empty and unique per run")
        with self._lock, self._transaction():
            status = self._connection.execute(
                "SELECT status FROM signal_review_runs WHERE run_id = ?", (run_id,),
            ).fetchone()
            if status is None or str(status[0]) != "running":
                raise ValueError("signal review run is not running")
            score_symbols = {str(value["symbol"]).upper() for value in scores}
            score_instrument_ids = self._instrument_ids(score_symbols)
            missing_score_symbols = score_symbols - score_instrument_ids.keys()
            if missing_score_symbols:
                raise ValueError(
                    "unknown scored instruments: " + ", ".join(sorted(missing_score_symbols))
                )
            for score in scores:
                symbol = str(score["symbol"]).upper()
                self._connection.execute(
                    """
                    INSERT INTO signal_review_scores(
                        run_id, entity_key, instrument_id, system_id, scorer_version,
                        entity_scope, eligible, total_score, grade, rank,
                        participant_count, ranking_universe_digest, verdict,
                        summary, risk_summary, change_summary, payload_json,
                        created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id, score.get("entity_key", symbol),
                        score_instrument_ids[symbol], score["system_id"],
                        score["scorer_version"], score["entity_scope"],
                        int(bool(score["eligible"])), float(score["total_score"]),
                        score["grade"], int(score["rank"]),
                        int(score["participant_count"]),
                        score["ranking_universe_digest"], score["verdict"],
                        score["summary"], score["risk_summary"],
                        score["change_summary"],
                        json.dumps(score, ensure_ascii=False, sort_keys=True), now_ms,
                    ),
                )
            for item in items:
                identity = self._canonical_instrument_identity(str(item["symbol"]))
                if identity is None:
                    raise ValueError(f"unknown signal instrument: {item['symbol']}")
                _, instrument_id = identity
                self._connection.execute(
                    """
                    INSERT INTO signal_review_items(
                        run_id, item_id, item_key, rank, instrument_id, profile,
                        change_type, active, score, confidence, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (run_id, item["item_id"], item["item_key"], item["rank"],
                     instrument_id, item["profile"], item["change_type"],
                     int(bool(item["active"])), float(item["score"]),
                     float(item["confidence"]),
                     json.dumps(item.get("payload", {}), ensure_ascii=False, sort_keys=True)),
                )
                for position, evidence in enumerate(item.get("evidence", []), 1):
                    self._connection.execute(
                        """
                        INSERT INTO signal_review_evidence(
                            run_id, item_id, evidence_id, alias, evidence_type,
                            source_run_id, source_item_id, payload_json, position
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (run_id, item["item_id"], evidence["evidence_id"],
                         evidence["alias"], evidence["evidence_type"],
                         evidence.get("source_run_id"), evidence.get("source_item_id"),
                         json.dumps(evidence.get("payload", {}), ensure_ascii=False,
                                    sort_keys=True), position),
                    )
            self._connection.execute(
                """
                UPDATE signal_review_runs
                SET status = 'succeeded', phase = 'completed', work_done = work_total,
                    item_count = ?, added_count = ?, retained_count = ?, removed_count = ?,
                    summary_json = ?, input_digest = ?, completed_at_ms = ?
                WHERE run_id = ?
                """,
                (sum(bool(item["active"]) for item in items), counts["added"],
                 counts["retained"], counts["removed"],
                 json.dumps(summary, ensure_ascii=False, sort_keys=True), input_digest,
                 now_ms, run_id),
            )
        return self.get_signal_review_run(run_id)  # type: ignore[return-value]

    def list_signal_review_scores(
        self, run_id: str, system_id: str | None = None,
        limit: int = 5000, offset: int = 0,
    ) -> list[dict[str, object]]:
        if not 1 <= limit <= 5000:
            raise ValueError("score limit must be between 1 and 5000")
        if offset < 0:
            raise ValueError("score offset must be non-negative")
        system_clause = "AND score.system_id = ?" if system_id else ""
        parameters: tuple[object, ...] = (
            (run_id, system_id, limit, offset) if system_id
            else (run_id, limit, offset)
        )
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT instrument.symbol, instrument.name, instrument.kind,
                       instrument.exchange, run.effective_date, score.payload_json
                FROM signal_review_scores AS score
                JOIN instruments AS instrument USING (instrument_id)
                JOIN signal_review_runs AS run USING (run_id)
                WHERE score.run_id = ? {system_clause}
                ORDER BY score.system_id, score.eligible DESC, score.rank,
                         instrument.symbol
                LIMIT ? OFFSET ?
                """, parameters,
            ).fetchall()
        return [{
            **json.loads(str(row[5])),
            "run_id": run_id, "symbol": str(row[0]), "name": str(row[1]),
            "kind": str(row[2]), "exchange": str(row[3]),
            "effective_date": _date_from_key(int(row[4])),
        } for row in rows]

    def count_signal_review_scores(
        self, run_id: str, system_id: str | None = None,
    ) -> int:
        clause = " AND system_id = ?" if system_id else ""
        parameters: tuple[object, ...] = (run_id, system_id) if system_id else (run_id,)
        with self._lock:
            return int(self._connection.execute(
                f"SELECT count(*) FROM signal_review_scores WHERE run_id = ?{clause}",
                parameters,
            ).fetchone()[0])

    def fail_signal_review_run(self, run_id: str, error: str) -> None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            self._connection.execute(
                """
                UPDATE signal_review_runs
                SET status = 'failed', phase = 'failed', error = ?, completed_at_ms = ?
                WHERE run_id = ? AND status = 'running'
                """, (" ".join(error.split())[:2000], now_ms, run_id),
            )

    def recover_interrupted_signal_review_runs(self) -> int:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            cursor = self._connection.execute(
                """
                UPDATE signal_review_runs SET status = 'failed', phase = 'failed',
                    error = 'application stopped during signal calculation',
                    completed_at_ms = ? WHERE status = 'running'
                """, (now_ms,),
            )
            return cursor.rowcount

    def list_signal_review_runs(
        self, signal_id: str | None = None, limit: int = 50,
    ) -> list[dict[str, object]]:
        where = "WHERE signal_id = ?" if signal_id else ""
        parameters: tuple[object, ...] = (signal_id, limit) if signal_id else (limit,)
        with self._lock:
            rows = self._connection.execute(
                f"""SELECT {_SIGNAL_RUN_COLUMNS} FROM signal_review_runs {where}
                ORDER BY started_at_ms DESC, run_id DESC LIMIT ?""", parameters,
            ).fetchall()
        return [_run_row(row) for row in rows]

    def get_signal_review_run(self, run_id: str) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                f"SELECT {_SIGNAL_RUN_COLUMNS} FROM signal_review_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return _run_row(row) if row else None

    def list_signal_review_items(
        self, run_id: str, limit: int | None = None, offset: int = 0,
    ) -> list[dict[str, object]]:
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        paging = " LIMIT ? OFFSET ?" if limit is not None else ""
        parameters: tuple[object, ...] = (
            (run_id, limit, offset) if limit is not None else (run_id,)
        )
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT item.item_id, item.item_key, item.rank, instrument.symbol,
                       instrument.name, instrument.kind, instrument.exchange,
                       item.profile, item.change_type, item.active, item.score,
                       item.confidence, item.payload_json
                FROM signal_review_items AS item
                JOIN instruments AS instrument USING (instrument_id)
                WHERE item.run_id = ?
                ORDER BY item.active DESC, item.profile, item.rank, instrument.symbol
                """ + paging, parameters,
            ).fetchall()
            item_ids = [str(row[0]) for row in rows]
            placeholders = ",".join("?" for _ in item_ids)
            evidence_rows = self._connection.execute(
                f"""
                SELECT item_id, evidence_id, alias, evidence_type, source_run_id,
                       source_item_id, payload_json
                FROM signal_review_evidence
                WHERE run_id = ? AND item_id IN ({placeholders})
                ORDER BY item_id, position
                """, (run_id, *item_ids),
            ).fetchall() if item_ids else []
        evidence: dict[str, list[dict[str, object]]] = {}
        for row in evidence_rows:
            evidence.setdefault(str(row[0]), []).append({
                "evidence_id": str(row[1]), "alias": str(row[2]),
                "evidence_type": str(row[3]), "source_run_id": row[4],
                "source_item_id": row[5], "payload": json.loads(str(row[6])),
            })
        return [{
            "item_id": str(row[0]), "item_key": str(row[1]), "rank": int(row[2]),
            "symbol": str(row[3]), "name": str(row[4]), "kind": str(row[5]),
            "exchange": str(row[6]), "profile": str(row[7]),
            "change_type": str(row[8]), "active": bool(row[9]),
            "score": float(row[10]), "confidence": float(row[11]),
            "payload": json.loads(str(row[12])), "evidence": evidence.get(str(row[0]), []),
        } for row in rows]

    def count_signal_review_items(self, run_id: str) -> int:
        with self._lock:
            return int(self._connection.execute(
                "SELECT count(*) FROM signal_review_items WHERE run_id = ?", (run_id,),
            ).fetchone()[0])

    def get_signal_review_item(
        self, run_id: str, item_id: str,
    ) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT item.item_id, item.item_key, item.rank, instrument.symbol,
                       instrument.name, instrument.kind, instrument.exchange,
                       item.profile, item.change_type, item.active, item.score,
                       item.confidence, item.payload_json
                FROM signal_review_items AS item
                JOIN instruments AS instrument USING (instrument_id)
                WHERE item.run_id = ? AND item.item_id = ?
                """, (run_id, item_id),
            ).fetchone()
            if row is None:
                return None
            evidence_rows = self._connection.execute(
                """
                SELECT evidence_id, alias, evidence_type, source_run_id,
                       source_item_id, payload_json
                FROM signal_review_evidence
                WHERE run_id = ? AND item_id = ? ORDER BY position
                """, (run_id, item_id),
            ).fetchall()
        return {
            "item_id": str(row[0]), "item_key": str(row[1]), "rank": int(row[2]),
            "symbol": str(row[3]), "name": str(row[4]), "kind": str(row[5]),
            "exchange": str(row[6]), "profile": str(row[7]),
            "change_type": str(row[8]), "active": bool(row[9]),
            "score": float(row[10]), "confidence": float(row[11]),
            "payload": json.loads(str(row[12])),
            "evidence": [{
                "evidence_id": str(value[0]), "alias": str(value[1]),
                "evidence_type": str(value[2]), "source_run_id": value[3],
                "source_item_id": value[4], "payload": json.loads(str(value[5])),
            } for value in evidence_rows],
        }

    def get_prior_signal_review_items(self, run_id: str) -> list[dict[str, object]]:
        with self._lock:
            row = self._connection.execute(
                "SELECT prior_run_id FROM signal_review_runs WHERE run_id = ?", (run_id,),
            ).fetchone()
        return self.list_signal_review_items(str(row[0])) if row and row[0] else []


_SIGNAL_RUN_COLUMNS = """
run_id, signal_id, definition_version, algorithm_version, cadence,
effective_date, revision, prior_run_id, parameters_json, input_digest,
status, phase, work_total, work_done, item_count, added_count,
retained_count, removed_count, summary_json, error, started_at_ms, completed_at_ms
"""


def _run_row(row) -> dict[str, object]:
    return {
        "run_id": str(row[0]), "signal_id": str(row[1]),
        "definition_version": str(row[2]), "algorithm_version": str(row[3]),
        "cadence": str(row[4]), "effective_date": _date_from_key(int(row[5])),
        "revision": int(row[6]), "prior_run_id": row[7],
        "parameters": json.loads(str(row[8])), "input_digest": row[9],
        "status": str(row[10]), "phase": str(row[11]),
        "work_total": int(row[12]), "work_done": int(row[13]),
        "item_count": int(row[14]), "added_count": int(row[15]),
        "retained_count": int(row[16]), "removed_count": int(row[17]),
        "summary": json.loads(str(row[18])), "error": row[19],
        "started_at_ms": int(row[20]),
        "completed_at_ms": int(row[21]) if row[21] is not None else None,
    }
