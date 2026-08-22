"""Generated-analysis and human-review SQLite storage mixin."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timezone
import json
import sqlite3
from uuid import uuid4

from stock_harness.analysis_results import (
    AnalysisNamespace,
    AnalysisRunRecord,
    AnalysisRunSpec,
    AnalysisRunStatus,
    ClaimedAnalysisTarget,
    GeneratedAnalysisItem,
    GeneratedAnalysisTarget,
    validate_items,
)
from stock_harness.sqlite_mapping import _date_from_key, _date_key
from stock_harness.trend_reviews import (
    TrendReviewDraftSpec,
    TrendReviewLabel,
    TrendReviewStatus,
)


_TREND_REVIEW_SELECT = """
SELECT review.review_id, instrument.symbol, review.schema_version,
       review.dataset_version, review.timeframe, review.horizon,
       review.interval_start, review.interval_end, review.as_of_date,
       review.input_digest, review.algorithm_version, review.config_version,
       review.settings_json, review.classification, review.review_status, review.tags_json,
       review.labels_json, review.expected_json, review.rationale,
       review.sources_json, review.revision,
       review.created_at_ms, review.updated_at_ms
FROM trend_review_cases AS review
JOIN instruments AS instrument USING (instrument_id)
"""


def _review_labels_json(labels: Sequence[TrendReviewLabel]) -> str:
    return json.dumps([
        {
            "item_id": label.item_id,
            "item_type": label.item_type,
            "decision": label.decision.value,
            "payload": label.payload,
            "rationale": label.rationale,
        }
        for label in labels
    ], ensure_ascii=False, sort_keys=True)


def _trend_review_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "review_id": str(row[0]),
        "symbol": str(row[1]),
        "schema_version": str(row[2]),
        "dataset_version": str(row[3]),
        "timeframe": str(row[4]),
        "horizon": str(row[5]),
        "interval_start": _date_from_key(int(row[6])),
        "interval_end": _date_from_key(int(row[7])),
        "as_of_date": _date_from_key(int(row[8])),
        "input_digest": bytes(row[9]),
        "algorithm_version": str(row[10]),
        "config_version": str(row[11]),
        "settings": json.loads(str(row[12])),
        "classification": str(row[13]),
        "review_status": str(row[14]),
        "tags": json.loads(str(row[15])),
        "labels": json.loads(str(row[16])),
        "expected": json.loads(str(row[17])),
        "rationale": str(row[18]),
        "sources": json.loads(str(row[19])),
        "revision": int(row[20]),
        "created_at_ms": int(row[21]),
        "updated_at_ms": int(row[22]),
    }


class SQLiteAnalysisStoreMixin:
    def create_trend_review(self, spec: TrendReviewDraftSpec) -> dict[str, object]:
        spec.validate()
        review_id = str(uuid4())
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            identity = self._canonical_instrument_identity(spec.symbol)
            if identity is None:
                raise ValueError(
                    f"trend review references unknown instrument: {spec.symbol.strip()}"
                )
            symbol, instrument_id = identity
            self._connection.execute(
                """
                INSERT INTO trend_review_cases(
                    review_id, instrument_id, schema_version, dataset_version,
                    timeframe, horizon, interval_start, interval_end, as_of_date,
                    input_digest, algorithm_version, config_version, settings_json,
                    classification, review_status, tags_json, labels_json,
                    expected_json, rationale, sources_json, revision,
                    created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    review_id, instrument_id, "1.0", spec.dataset_version.strip(),
                    spec.timeframe, spec.horizon, _date_key(spec.interval_start),
                    _date_key(spec.interval_end), _date_key(spec.as_of_date),
                    spec.input_digest, spec.algorithm_version.strip(), spec.config_version.strip(),
                    json.dumps(spec.settings, ensure_ascii=False, sort_keys=True),
                    spec.classification.strip(), spec.status.value,
                    json.dumps(list(dict.fromkeys(spec.tags)), ensure_ascii=False),
                    _review_labels_json(spec.labels),
                    json.dumps(spec.expected, ensure_ascii=False, sort_keys=True),
                    spec.rationale.strip(),
                    json.dumps(list(spec.sources), ensure_ascii=False, sort_keys=True),
                    now_ms, now_ms,
                ),
            )
        created = self.get_trend_review(review_id)
        if created is None:
            raise RuntimeError("created trend review is not readable")
        return created

    def get_trend_review(self, review_id: str) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                f"{_TREND_REVIEW_SELECT} WHERE review.review_id = ?",
                (review_id,),
            ).fetchone()
        return _trend_review_row(row) if row is not None else None

    def list_trend_reviews(
        self,
        *,
        symbol: str | None = None,
        status: TrendReviewStatus | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        if not 1 <= limit <= 500:
            raise ValueError("trend review limit must be between 1 and 500")
        clauses: list[str] = []
        params: list[object] = []
        if symbol:
            clauses.append("instrument.symbol = ? COLLATE NOCASE")
            params.append(symbol.upper())
        if status is not None:
            clauses.append("review.review_status = ?")
            params.append(status.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                f"{_TREND_REVIEW_SELECT} {where} "
                "ORDER BY review.updated_at_ms DESC, review.review_id LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [_trend_review_row(row) for row in rows]

    def update_trend_review(
        self,
        review_id: str,
        *,
        expected_revision: int,
        status: TrendReviewStatus,
        labels: Sequence[TrendReviewLabel],
        expected: dict[str, object],
        rationale: str,
    ) -> dict[str, object]:
        ids = [label.item_id for label in labels]
        if len(ids) != len(set(ids)):
            raise ValueError("trend review label item IDs must be unique")
        for label in labels:
            label.validate()
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            current = self._connection.execute(
                "SELECT review_status, labels_json FROM trend_review_cases WHERE review_id = ?",
                (review_id,),
            ).fetchone()
            if current is None:
                raise ValueError("trend review not found")
            if str(current[0]) == TrendReviewStatus.CONFIRMED.value:
                raise ValueError("confirmed trend review is immutable")
            original_labels = json.loads(str(current[1]))
            original_types = {
                str(item["item_id"]): str(item["item_type"])
                for item in original_labels
            }
            updated_types = {label.item_id: label.item_type for label in labels}
            if any(
                updated_types.get(item_id) != item_type
                for item_id, item_type in original_types.items()
            ):
                raise ValueError("trend review update must retain every original candidate")
            cursor = self._connection.execute(
                """
                UPDATE trend_review_cases
                SET review_status = ?, labels_json = ?, expected_json = ?,
                    rationale = ?, revision = revision + 1, updated_at_ms = ?
                WHERE review_id = ? AND revision = ?
                """,
                (
                    status.value, _review_labels_json(labels),
                    json.dumps(expected, ensure_ascii=False, sort_keys=True),
                    rationale.strip(), now_ms, review_id, expected_revision,
                ),
            )
            if cursor.rowcount == 0:
                raise RuntimeError("trend review revision conflict")
        updated = self.get_trend_review(review_id)
        if updated is None:
            raise RuntimeError("updated trend review is not readable")
        return updated

    def begin_generated_analysis_run(
        self, spec: AnalysisRunSpec
    ) -> AnalysisRunRecord:
        spec.validate()
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            instrument = self._canonical_instrument_identity(spec.symbol)
            if instrument is None:
                raise ValueError(
                    f"analysis references unknown instrument: {spec.symbol.strip()}"
                )
            normalized_symbol, instrument_id = instrument
            identity = (
                instrument_id,
                spec.system_id.strip(),
                spec.timeframe.strip(),
                spec.namespace.value,
                _date_key(spec.as_of_date),
                spec.input_digest,
                spec.algorithm_version.strip(),
                spec.config_version.strip(),
            )
            existing = self._connection.execute(
                """
                SELECT run_id, attempt, supersedes_run_id
                FROM generated_analysis_runs
                WHERE instrument_id = ? AND system_id = ? AND timeframe = ?
                  AND namespace = ? AND as_of_date = ? AND input_digest = ?
                  AND algorithm_version = ? AND config_version = ?
                  AND status = 'succeeded'
                ORDER BY completed_at_ms DESC LIMIT 1
                """,
                identity,
            ).fetchone()
            if existing is not None:
                return AnalysisRunRecord(
                    run_id=str(existing[0]),
                    status=AnalysisRunStatus.SUCCEEDED,
                    attempt=int(existing[1]),
                    supersedes_run_id=str(existing[2]) if existing[2] else None,
                    reused=True,
                )
            attempt = int(self._connection.execute(
                """
                SELECT coalesce(max(attempt), 0) + 1
                FROM generated_analysis_runs
                WHERE instrument_id = ? AND system_id = ? AND timeframe = ?
                  AND namespace = ? AND as_of_date = ? AND input_digest = ?
                  AND algorithm_version = ? AND config_version = ?
                """,
                identity,
            ).fetchone()[0])
            superseded = self._connection.execute(
                """
                SELECT run_id FROM generated_analysis_runs
                WHERE instrument_id = ? AND system_id = ? AND timeframe = ?
                  AND namespace = ? AND status = 'succeeded'
                ORDER BY as_of_date DESC, completed_at_ms DESC LIMIT 1
                """,
                identity[:4],
            ).fetchone()
            run_id = uuid4().hex
            supersedes_run_id = str(superseded[0]) if superseded else None
            self._connection.execute(
                """
                INSERT INTO generated_analysis_runs(
                    run_id, system_id, instrument_id, timeframe, namespace,
                    as_of_date, input_start_date, input_end_date, input_digest,
                    algorithm_version, config_version, completion_state, status,
                    attempt, source_observed_at_ms, expires_at_ms,
                    supersedes_run_id, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)
                """,
                (
                    run_id, identity[1], instrument_id, identity[2], identity[3],
                    identity[4], _date_key(spec.input_start_date),
                    _date_key(spec.input_end_date), spec.input_digest, identity[6],
                    identity[7], spec.completion_state, attempt,
                    spec.source_observed_at_ms, spec.expires_at_ms,
                    supersedes_run_id, now_ms,
                ),
            )
        return AnalysisRunRecord(
            run_id, AnalysisRunStatus.RUNNING, attempt, supersedes_run_id
        )

    def upsert_generated_analysis_target(
        self, target: GeneratedAnalysisTarget
    ) -> int:
        target.validate()
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            instrument = self._canonical_instrument_identity(target.symbol)
            if instrument is None:
                raise ValueError(
                    f"analysis target references unknown instrument: {target.symbol.strip()}"
                )
            symbol, instrument_id = instrument
            target_id = int(self._connection.execute(
                """
                INSERT INTO generated_analysis_targets(
                    instrument_id, system_id, timeframe, algorithm_version,
                    config_version, settings_json, enabled, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id, system_id, timeframe) DO UPDATE SET
                    algorithm_version = excluded.algorithm_version,
                    config_version = excluded.config_version,
                    settings_json = excluded.settings_json,
                    enabled = excluded.enabled,
                    updated_at_ms = excluded.updated_at_ms
                RETURNING target_id
                """,
                (
                    instrument_id, target.system_id.strip(),
                    target.timeframe.strip(), target.algorithm_version.strip(),
                    target.config_version.strip(),
                    json.dumps(target.settings, sort_keys=True, separators=(",", ":")),
                    int(target.enabled), now_ms,
                ),
            ).fetchone()[0])
            if not target.enabled:
                self._connection.execute(
                    "DELETE FROM generated_analysis_dirty_targets WHERE target_id = ?",
                    (target_id,),
                )
        return target_id

    def queue_generated_analysis_target(
        self,
        symbol: str,
        system_id: str,
        timeframe: str,
        dirty_from: date,
        dirty_through: date,
        reason: str,
    ) -> bool:
        if dirty_from > dirty_through:
            raise ValueError("analysis dirty range is invalid")
        if not reason.strip():
            raise ValueError("analysis dirty reason is required")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            target = self._connection.execute(
                """
                SELECT target.target_id
                FROM generated_analysis_targets AS target
                JOIN instruments AS instrument USING (instrument_id)
                WHERE instrument.symbol = ? COLLATE NOCASE
                  AND target.system_id = ? AND target.timeframe = ?
                  AND target.enabled = 1
                """,
                (symbol.upper(), system_id, timeframe),
            ).fetchone()
            if target is None:
                return False
            self._connection.execute(
                """
                INSERT INTO generated_analysis_dirty_targets(
                    target_id, dirty_from, dirty_through, reason,
                    generation, queued_at_ms
                ) VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(target_id) DO UPDATE SET
                    dirty_from = min(dirty_from, excluded.dirty_from),
                    dirty_through = max(dirty_through, excluded.dirty_through),
                    reason = excluded.reason, generation = generation + 1,
                    queued_at_ms = excluded.queued_at_ms,
                    claimed_at_ms = NULL, lease_until_ms = NULL
                """,
                (
                    int(target[0]), _date_key(dirty_from), _date_key(dirty_through),
                    reason.strip(), now_ms,
                ),
            )
        return True

    def claim_generated_analysis_targets(
        self, *, limit: int = 10, lease_ms: int = 60_000
    ) -> list[ClaimedAnalysisTarget]:
        if not 1 <= limit <= 100 or lease_ms <= 0:
            raise ValueError("invalid analysis claim limit or lease")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        lease_until_ms = now_ms + lease_ms
        with self._lock, self._transaction():
            rows = self._connection.execute(
                """
                SELECT dirty.target_id, instrument.symbol, target.system_id,
                       target.timeframe, target.algorithm_version,
                       target.config_version, target.settings_json, dirty.dirty_from,
                       dirty.dirty_through, dirty.reason, dirty.generation
                FROM generated_analysis_dirty_targets AS dirty
                JOIN generated_analysis_targets AS target USING (target_id)
                JOIN instruments AS instrument USING (instrument_id)
                WHERE target.enabled = 1
                  AND (dirty.lease_until_ms IS NULL OR dirty.lease_until_ms <= ?)
                ORDER BY dirty.queued_at_ms, dirty.target_id LIMIT ?
                """,
                (now_ms, limit),
            ).fetchall()
            self._connection.executemany(
                """
                UPDATE generated_analysis_dirty_targets
                SET claimed_at_ms = ?, lease_until_ms = ?, last_error = NULL
                WHERE target_id = ? AND generation = ?
                """,
                ((now_ms, lease_until_ms, int(row[0]), int(row[10])) for row in rows),
            )
        return [
            ClaimedAnalysisTarget(
                target_id=int(row[0]), symbol=str(row[1]), system_id=str(row[2]),
                timeframe=str(row[3]), algorithm_version=str(row[4]),
                config_version=str(row[5]), settings=json.loads(str(row[6])),
                dirty_from=_date_from_key(int(row[7])),
                dirty_through=_date_from_key(int(row[8])), reason=str(row[9]),
                generation=int(row[10]),
            )
            for row in rows
        ]

    def claim_generated_analysis_target(
        self, target_id: int, *, lease_ms: int = 60_000
    ) -> ClaimedAnalysisTarget | None:
        if lease_ms <= 0:
            raise ValueError("analysis claim lease must be positive")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            row = self._connection.execute(
                """
                SELECT dirty.target_id, instrument.symbol, target.system_id,
                       target.timeframe, target.algorithm_version,
                       target.config_version, target.settings_json, dirty.dirty_from,
                       dirty.dirty_through, dirty.reason, dirty.generation
                FROM generated_analysis_dirty_targets AS dirty
                JOIN generated_analysis_targets AS target USING (target_id)
                JOIN instruments AS instrument USING (instrument_id)
                WHERE dirty.target_id = ? AND target.enabled = 1
                  AND (dirty.lease_until_ms IS NULL OR dirty.lease_until_ms <= ?)
                """,
                (target_id, now_ms),
            ).fetchone()
            if row is None:
                return None
            self._connection.execute(
                """
                UPDATE generated_analysis_dirty_targets
                SET claimed_at_ms = ?, lease_until_ms = ?, last_error = NULL
                WHERE target_id = ? AND generation = ?
                """,
                (now_ms, now_ms + lease_ms, target_id, int(row[10])),
            )
        return ClaimedAnalysisTarget(
            target_id=int(row[0]), symbol=str(row[1]), system_id=str(row[2]),
            timeframe=str(row[3]), algorithm_version=str(row[4]),
            config_version=str(row[5]), settings=json.loads(str(row[6])),
            dirty_from=_date_from_key(int(row[7])),
            dirty_through=_date_from_key(int(row[8])), reason=str(row[9]),
            generation=int(row[10]),
        )

    def complete_generated_analysis_target(
        self, target_id: int, generation: int
    ) -> bool:
        with self._lock, self._transaction():
            before = self._connection.total_changes
            self._connection.execute(
                """
                DELETE FROM generated_analysis_dirty_targets
                WHERE target_id = ? AND generation = ?
                """,
                (target_id, generation),
            )
            return self._connection.total_changes > before

    def fail_generated_analysis_target(
        self, target_id: int, generation: int, error: str
    ) -> bool:
        if not error.strip():
            raise ValueError("analysis target failure is required")
        with self._lock, self._transaction():
            before = self._connection.total_changes
            self._connection.execute(
                """
                UPDATE generated_analysis_dirty_targets
                SET claimed_at_ms = NULL, lease_until_ms = NULL, last_error = ?
                WHERE target_id = ? AND generation = ?
                """,
                (error.strip(), target_id, generation),
            )
            return self._connection.total_changes > before

    def complete_generated_analysis_run(
        self,
        run_id: str,
        items: Sequence[GeneratedAnalysisItem],
        *,
        duration_ms: float,
        warnings: Sequence[dict[str, object]] = (),
    ) -> AnalysisRunRecord:
        if duration_ms < 0:
            raise ValueError("analysis duration must be non-negative")
        validate_items(items)
        serialized_items = [
            json.dumps(item.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for item in items
        ]
        warnings_json = json.dumps(
            list(warnings), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            row = self._connection.execute(
                """
                SELECT status, attempt, supersedes_run_id, namespace,
                       instrument_id, system_id, timeframe, as_of_date
                FROM generated_analysis_runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown analysis run: {run_id}")
            if str(row[0]) != AnalysisRunStatus.RUNNING.value:
                raise ValueError("only a running analysis can be completed")
            self._connection.executemany(
                """
                INSERT INTO generated_analysis_items(
                    run_id, item_id, item_type, parent_item_id, sequence, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        run_id, item.item_id, item.item_type.value,
                        item.parent_item_id, sequence, serialized_items[sequence],
                    )
                    for sequence, item in enumerate(items)
                ),
            )
            self._connection.execute(
                """
                UPDATE generated_analysis_runs
                SET status = 'succeeded', duration_ms = ?, warnings_json = ?,
                    completed_at_ms = ?, failure_details = NULL
                WHERE run_id = ?
                """,
                (duration_ms, warnings_json, now_ms, run_id),
            )
            if str(row[3]) == AnalysisNamespace.OFFICIAL.value:
                self._connection.execute(
                    """
                    UPDATE generated_analysis_runs
                    SET expires_at_ms = CASE
                        WHEN expires_at_ms IS NULL OR expires_at_ms > ? THEN ?
                        ELSE expires_at_ms END
                    WHERE instrument_id = ? AND system_id = ? AND timeframe = ?
                      AND namespace = 'preview' AND as_of_date <= ?
                    """,
                    (now_ms, now_ms, int(row[4]), str(row[5]), str(row[6]), int(row[7])),
                )
        return AnalysisRunRecord(
            run_id, AnalysisRunStatus.SUCCEEDED, int(row[1]),
            str(row[2]) if row[2] else None,
        )

    def fail_generated_analysis_run(
        self, run_id: str, failure_details: str, *, duration_ms: float
    ) -> AnalysisRunRecord:
        if not failure_details.strip():
            raise ValueError("analysis failure details are required")
        if duration_ms < 0:
            raise ValueError("analysis duration must be non-negative")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            row = self._connection.execute(
                """
                SELECT status, attempt, supersedes_run_id
                FROM generated_analysis_runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown analysis run: {run_id}")
            if str(row[0]) != AnalysisRunStatus.RUNNING.value:
                raise ValueError("only a running analysis can fail")
            self._connection.execute(
                """
                UPDATE generated_analysis_runs
                SET status = 'failed', duration_ms = ?, failure_details = ?,
                    completed_at_ms = ? WHERE run_id = ?
                """,
                (duration_ms, failure_details.strip(), now_ms, run_id),
            )
        return AnalysisRunRecord(
            run_id, AnalysisRunStatus.FAILED, int(row[1]),
            str(row[2]) if row[2] else None,
        )

    def get_latest_generated_analysis_run(
        self,
        symbol: str,
        system_id: str,
        timeframe: str,
        namespace: AnalysisNamespace = AnalysisNamespace.OFFICIAL,
    ) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT run.run_id, run.status, run.as_of_date,
                       run.input_start_date, run.input_end_date, run.input_digest,
                       run.algorithm_version, run.config_version,
                       run.completion_state, run.attempt, run.duration_ms,
                       run.warnings_json, run.failure_details,
                       run.source_observed_at_ms, run.expires_at_ms,
                       run.supersedes_run_id, run.created_at_ms, run.completed_at_ms
                FROM generated_analysis_runs AS run
                JOIN instruments AS instrument USING (instrument_id)
                WHERE instrument.symbol = ? COLLATE NOCASE
                  AND run.system_id = ? AND run.timeframe = ?
                  AND run.namespace = ? AND run.status = 'succeeded'
                ORDER BY run.as_of_date DESC, run.completed_at_ms DESC LIMIT 1
                """,
                (symbol.upper(), system_id, timeframe, namespace.value),
            ).fetchone()
            if row is None:
                return None
            items = self._connection.execute(
                """
                SELECT item_id, item_type, parent_item_id, payload_json
                FROM generated_analysis_items WHERE run_id = ? ORDER BY sequence
                """,
                (str(row[0]),),
            ).fetchall()
            lifecycle = self._connection.execute(
                """
                SELECT dirty.target_id IS NOT NULL,
                       target.algorithm_version, target.config_version,
                       EXISTS (
                           SELECT 1 FROM generated_analysis_runs AS newer
                           WHERE newer.instrument_id = target.instrument_id
                             AND newer.system_id = target.system_id
                             AND newer.timeframe = target.timeframe
                             AND newer.namespace = ?
                             AND newer.status IN ('running', 'failed')
                             AND (
                                 newer.as_of_date > ?
                                 OR (newer.as_of_date = ? AND newer.created_at_ms >= ?)
                             )
                       )
                FROM generated_analysis_targets AS target
                LEFT JOIN generated_analysis_dirty_targets AS dirty USING (target_id)
                JOIN instruments AS instrument USING (instrument_id)
                WHERE instrument.symbol = ? COLLATE NOCASE
                  AND target.system_id = ? AND target.timeframe = ?
                """,
                (
                    namespace.value, int(row[2]), int(row[2]), int(row[17]), symbol.upper(),
                    system_id, timeframe,
                ),
            ).fetchone()
        stale_reasons: list[str] = []
        if lifecycle is not None:
            if bool(lifecycle[0]):
                stale_reasons.append("canonical-input-dirty")
            if str(lifecycle[1]) != str(row[6]) or str(lifecycle[2]) != str(row[7]):
                stale_reasons.append("algorithm-or-config-upgraded")
            if bool(lifecycle[3]):
                stale_reasons.append("newer-run-incomplete-or-failed")
        return {
            "run_id": str(row[0]), "status": str(row[1]),
            "as_of_date": _date_from_key(int(row[2])),
            "input_start_date": _date_from_key(int(row[3])),
            "input_end_date": _date_from_key(int(row[4])),
            "input_digest": bytes(row[5]), "algorithm_version": str(row[6]),
            "config_version": str(row[7]), "completion_state": str(row[8]),
            "attempt": int(row[9]), "duration_ms": float(row[10]),
            "warnings": json.loads(str(row[11])), "failure_details": row[12],
            "source_observed_at_ms": row[13], "expires_at_ms": row[14],
            "supersedes_run_id": row[15], "created_at_ms": int(row[16]),
            "completed_at_ms": int(row[17]),
            "stale": bool(stale_reasons), "stale_reasons": stale_reasons,
            "items": [
                {
                    "item_id": str(item[0]), "item_type": str(item[1]),
                    "parent_item_id": item[2], "payload": json.loads(str(item[3])),
                }
                for item in items
            ],
        }

    def prune_generated_analysis_runs(
        self,
        now_ms: int,
        *,
        preview_retention_ms: int = 7 * 24 * 60 * 60 * 1000,
        failed_retention_ms: int = 30 * 24 * 60 * 60 * 1000,
    ) -> int:
        if min(now_ms, preview_retention_ms, failed_retention_ms) < 0:
            raise ValueError("analysis retention values must be non-negative")
        with self._lock, self._transaction():
            rows = self._connection.execute(
                """
                SELECT run_id FROM generated_analysis_runs
                WHERE (
                    namespace = 'preview' AND expires_at_ms IS NOT NULL
                    AND expires_at_ms <= ?
                ) OR (
                    status = 'failed' AND completed_at_ms IS NOT NULL
                    AND completed_at_ms <= ?
                )
                """,
                (now_ms - preview_retention_ms, now_ms - failed_retention_ms),
            ).fetchall()
            run_ids = [str(row[0]) for row in rows]
            if not run_ids:
                return 0
            placeholders = ",".join("?" for _ in run_ids)
            self._connection.execute(
                f"UPDATE generated_analysis_runs SET supersedes_run_id = NULL "
                f"WHERE supersedes_run_id IN ({placeholders})",
                run_ids,
            )
            self._connection.execute(
                f"DELETE FROM generated_analysis_runs WHERE run_id IN ({placeholders})",
                run_ids,
            )
        return len(run_ids)
