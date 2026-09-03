"""Provider coverage, validation, and repair persistence."""

from __future__ import annotations

from datetime import date, datetime, timezone

from stock_harness.models import CoverageGap, ProviderIncident, RepairJob, ValidationResult
from stock_harness.sqlite_mapping import _date_from_key, _date_key


class SQLiteProviderQualityStoreMixin:
    def has_daily_snapshot(self, source: str, scope: str, trade_date: date) -> bool:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1
                FROM daily_snapshot_receipts AS receipt
                JOIN sources AS source USING (source_id)
                WHERE source.code = ? AND receipt.scope = ? AND receipt.trade_date = ?
                """,
                (source, scope, _date_key(trade_date)),
            ).fetchone()
        return row is not None

    def list_daily_snapshot_dates(
        self,
        source: str,
        scope: str,
        start_date: date,
        end_date: date,
    ) -> set[date]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT receipt.trade_date
                FROM daily_snapshot_receipts AS receipt
                JOIN sources AS source USING (source_id)
                WHERE source.code = ? AND receipt.scope = ?
                  AND receipt.trade_date BETWEEN ? AND ?
                """,
                (source, scope, _date_key(start_date), _date_key(end_date)),
            ).fetchall()
        return {_date_from_key(int(row[0])) for row in rows}

    def record_coverage_gap(self, source: str, scope: str, trade_date: date, reason: str) -> None:
        if not reason:
            raise ValueError("coverage gap reason is required")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            source_id = self._source_id(source)
            self._connection.execute(
                """
                INSERT INTO coverage_gaps(source_id, scope, trade_date, reason, observed_at_ms)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id, scope, trade_date) DO UPDATE SET
                    reason = excluded.reason,
                    observed_at_ms = excluded.observed_at_ms
                """,
                (source_id, scope, _date_key(trade_date), reason, now_ms),
            )

    def list_coverage_gaps(self, source: str, scope: str) -> list[CoverageGap]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT source.code, gap.scope, gap.trade_date, gap.reason, gap.observed_at_ms
                FROM coverage_gaps AS gap
                JOIN sources AS source USING (source_id)
                WHERE source.code = ? AND gap.scope = ?
                ORDER BY gap.trade_date
                """,
                (source, scope),
            ).fetchall()
        return [
            CoverageGap(row[0], row[1], _date_from_key(row[2]), row[3], row[4])
            for row in rows
        ]

    def record_provider_incident(
        self,
        source: str,
        dataset: str,
        scope: str,
        trade_date: date,
        incident_type: str,
        message: str,
    ) -> int:
        if not all((dataset, scope, incident_type, message)):
            raise ValueError("provider incident fields are required")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            source_id = self._source_id(source)
            self._connection.execute(
                """
                INSERT INTO provider_incidents(
                    source_id, dataset, scope, trade_date, incident_type, status,
                    occurrence_count, message, first_observed_at_ms, last_observed_at_ms
                ) VALUES (?, ?, ?, ?, ?, 'open', 1, ?, ?, ?)
                ON CONFLICT(source_id, dataset, scope, trade_date, incident_type) DO UPDATE SET
                    status = 'open',
                    occurrence_count = provider_incidents.occurrence_count + 1,
                    message = excluded.message,
                    last_observed_at_ms = excluded.last_observed_at_ms,
                    resolved_at_ms = NULL,
                    resolution = NULL
                """,
                (
                    source_id,
                    dataset,
                    scope,
                    _date_key(trade_date),
                    incident_type,
                    message,
                    now_ms,
                    now_ms,

                ),
            )
            row = self._connection.execute(
                """
                SELECT incident_id FROM provider_incidents
                WHERE source_id = ? AND dataset = ? AND scope = ?
                  AND trade_date = ? AND incident_type = ?
                """,
                (source_id, dataset, scope, _date_key(trade_date), incident_type),
            ).fetchone()
        return int(row[0])

    def resolve_provider_incident(
        self,
        source: str,
        dataset: str,
        scope: str,
        trade_date: date,
        incident_type: str,
        resolution: str,
    ) -> bool:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            source_id = self._source_id(source)
            cursor = self._connection.execute(
                """
                UPDATE provider_incidents
                SET status = 'resolved', resolved_at_ms = ?, resolution = ?
                WHERE source_id = ? AND dataset = ? AND scope = ?
                  AND trade_date = ? AND incident_type = ? AND status = 'open'
                """,
                (
                    now_ms,
                    resolution,
                    source_id,
                    dataset,
                    scope,
                    _date_key(trade_date),
                    incident_type,
                ),
            )
        return cursor.rowcount > 0

    def list_provider_incidents(self) -> list[ProviderIncident]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT incident.incident_id, source.code, incident.dataset, incident.scope,
                       incident.trade_date, incident.incident_type, incident.status,
                       incident.occurrence_count, incident.message,
                       incident.first_observed_at_ms, incident.last_observed_at_ms,
                       incident.resolved_at_ms, incident.resolution
                FROM provider_incidents AS incident
                JOIN sources AS source USING (source_id)
                ORDER BY incident.trade_date, incident.incident_id
                """
            ).fetchall()
        return [
            ProviderIncident(
                int(row[0]), str(row[1]), str(row[2]), str(row[3]), _date_from_key(row[4]),
                str(row[5]), str(row[6]), int(row[7]), str(row[8]), int(row[9]), int(row[10]),
                int(row[11]) if row[11] is not None else None,
                str(row[12]) if row[12] is not None else None,
            )
            for row in rows
        ]

    def record_validation_result(
        self,
        primary_source: str,
        validator_source: str,
        symbol: str,
        trade_date: date,
        status: str,
        message: str,
        incident_id: int | None = None,
    ) -> None:
        if status not in {"match", "mismatch", "missing", "error"}:
            raise ValueError(f"unsupported validation status: {status}")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            primary_id = self._source_id(primary_source)
            validator_id = self._source_id(validator_source)
            self._connection.execute(
                """
                INSERT INTO provider_validation_results(
                    incident_id, primary_source_id, validator_source_id, symbol,
                    trade_date, status, message, checked_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    incident_id, primary_id, validator_id, symbol, _date_key(trade_date),
                    status, message, now_ms,
                ),
            )

    def list_validation_results(self, trade_date: date) -> list[ValidationResult]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT primary_source.code, validator_source.code, result.symbol,
                       result.trade_date, result.status, result.message, result.checked_at_ms
                FROM provider_validation_results AS result
                JOIN sources AS primary_source
                  ON primary_source.source_id = result.primary_source_id
                JOIN sources AS validator_source
                  ON validator_source.source_id = result.validator_source_id
                WHERE result.trade_date = ?
                ORDER BY result.symbol, validator_source.code, result.validation_id
                """,
                (_date_key(trade_date),),
            ).fetchall()
        return [
            ValidationResult(
                str(row[0]), str(row[1]), str(row[2]), _date_from_key(row[3]),
                str(row[4]), str(row[5]), int(row[6]),
            )
            for row in rows
        ]

    def enqueue_repair_job(self, primary_source: str, scope: str, trade_date: date) -> int:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            source_id = self._source_id(primary_source)
            self._connection.execute(
                """
                INSERT OR IGNORE INTO repair_jobs(
                    primary_source_id, scope, trade_date, status, created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, 'queued', ?, ?)
                """,
                (source_id, scope, _date_key(trade_date), now_ms, now_ms),
            )
            row = self._connection.execute(
                """
                SELECT job_id FROM repair_jobs
                WHERE primary_source_id = ? AND scope = ? AND trade_date = ?
                """,
                (source_id, scope, _date_key(trade_date)),
            ).fetchone()
        return int(row[0])

    def begin_repair_job(self, primary_source: str, scope: str, trade_date: date) -> int:
        job_id = self.enqueue_repair_job(primary_source, scope, trade_date)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            self._connection.execute(
                """
                UPDATE repair_jobs
                SET status = 'running', attempt_count = attempt_count + 1,
                    last_error = NULL, updated_at_ms = ?
                WHERE job_id = ?
                """,
                (now_ms, job_id),
            )
        return job_id

    def record_repair_items(
        self,
        job_id: int,
        repair_source: str | None,
        status: str,
        messages: dict[str, str],
    ) -> None:
        if status not in {"repaired", "unresolved"}:
            raise ValueError(f"unsupported repair item status: {status}")
        if not messages:
            return
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            source_id = self._source_id(repair_source) if repair_source else None
            self._connection.executemany(
                """
                INSERT INTO repair_items(
                    job_id, symbol, repair_source_id, status, message, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, symbol) DO UPDATE SET
                    repair_source_id = excluded.repair_source_id,
                    status = excluded.status,
                    message = excluded.message,
                    updated_at_ms = excluded.updated_at_ms
                """,
                [
                    (job_id, symbol, source_id, status, message, now_ms)
                    for symbol, message in messages.items()
                ],
            )

    def queue_symbol_repairs(
        self,
        primary_source: str,
        scope: str,
        trade_date: date,
        messages: dict[str, str],
    ) -> int:
        job_id = self.enqueue_repair_job(primary_source, scope, trade_date)
        self.record_repair_items(job_id, None, "unresolved", messages)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            self._connection.execute(
                """
                UPDATE repair_jobs
                SET status = 'queued', expected_rows = ?, repaired_rows = 0,
                    unresolved_rows = ?, last_error = NULL, updated_at_ms = ?
                WHERE job_id = ?
                """,
                (len(messages), len(messages), now_ms, job_id),
            )
        return job_id

    def list_unresolved_repair_symbols(self, job_id: int) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT symbol FROM repair_items
                WHERE job_id = ? AND status = 'unresolved'
                ORDER BY symbol
                """,
                (job_id,),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def finish_repair_job(
        self,
        job_id: int,
        status: str,
        expected_rows: int,
        repaired_rows: int,
        unresolved_rows: int,
        last_error: str | None = None,
    ) -> None:
        if status not in {"completed", "partial", "failed"}:
            raise ValueError(f"unsupported repair job status: {status}")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            self._connection.execute(
                """
                UPDATE repair_jobs
                SET status = ?, expected_rows = ?, repaired_rows = ?,
                    unresolved_rows = ?, last_error = ?, updated_at_ms = ?
                WHERE job_id = ?
                  AND (status != 'completed' OR ? = 'completed')
                """,
                (
                    status, expected_rows, repaired_rows, unresolved_rows,
                    last_error, now_ms, job_id, status,
                ),
            )

    def list_repair_jobs(self, statuses: set[str] | None = None) -> list[RepairJob]:
        clauses = []

        parameters: list[object] = []
        if statuses:
            ordered = sorted(statuses)
            clauses.append("job.status IN (" + ",".join("?" for _ in ordered) + ")")
            parameters.extend(ordered)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT job.job_id, source.code, job.scope, job.trade_date, job.status,
                       job.attempt_count, job.expected_rows, job.repaired_rows,
                       job.unresolved_rows, job.last_error,
                       job.created_at_ms, job.updated_at_ms
                FROM repair_jobs AS job
                JOIN sources AS source ON source.source_id = job.primary_source_id
                {where}
                ORDER BY job.trade_date, job.job_id
                """,
                parameters,
            ).fetchall()
        return [
            RepairJob(
                int(row[0]), str(row[1]), str(row[2]), _date_from_key(row[3]),
                str(row[4]), int(row[5]), int(row[6]), int(row[7]), int(row[8]),
                str(row[9]) if row[9] is not None else None, int(row[10]), int(row[11]),
            )
            for row in rows
        ]


