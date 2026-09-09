"""Persistence for complete board observations and compact attention state."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timezone
import json

from stock_harness.models import StockDailyLimit, StoredDailyBar
from stock_harness.sqlite_mapping import _date_from_key, _date_key


class SQLiteSignalObservationStoreMixin:
    def list_stock_board_memberships_many(
        self, symbols: Sequence[str], board_limit: int = 3,
    ) -> dict[str, list[dict[str, object]]]:
        ordered = list(dict.fromkeys(
            symbol.strip().upper() for symbol in symbols if symbol.strip()
        ))
        if len(ordered) > 200:
            raise ValueError("bulk board membership query exceeds 200 symbols")
        if not 1 <= board_limit <= 10:
            raise ValueError("board membership limit must be between 1 and 10")
        if not ordered:
            return {}
        placeholders = ",".join("?" for _ in ordered)
        with self._lock:
            rows = self._connection.execute(
                f"""
                WITH candidates AS (
                    SELECT membership.member_symbol, board.symbol, board.name,
                           min(source.code) AS code,
                           min(catalog.family) AS family,
                           min(catalog.category) AS category
                    FROM board_memberships AS membership
                    JOIN instruments AS board
                      ON board.instrument_id = membership.board_instrument_id
                    JOIN sources AS source USING (source_id)
                    LEFT JOIN instrument_catalog_entries AS catalog
                      ON catalog.instrument_id = board.instrument_id
                     AND catalog.catalog_source_id = membership.source_id
                    WHERE membership.active = 1 AND board.active = 1
                      AND membership.member_symbol IN ({placeholders})
                    GROUP BY membership.member_symbol, board.instrument_id
                ), ranked AS (
                    SELECT member_symbol, symbol, name, code, family, category,
                           row_number() OVER (
                               PARTITION BY member_symbol
                               ORDER BY
                                 CASE
                                   WHEN lower(coalesce(category, '')) = 'industry' THEN 0
                                   WHEN lower(coalesce(category, '')) = 'concept' THEN 1
                                   ELSE 2
                                 END,
                                 symbol
                           ) AS position
                    FROM candidates
                )
                SELECT member_symbol, symbol, name, code, family, category
                FROM ranked WHERE position <= ?
                ORDER BY member_symbol, position
                """, (*ordered, board_limit),
            ).fetchall()
        result: dict[str, list[dict[str, object]]] = {
            symbol: [] for symbol in ordered
        }
        for row in rows:
            result.setdefault(str(row[0]), []).append({
                "symbol": str(row[1]), "name": str(row[2]),
                "source": str(row[3]),
                "family": str(row[4]) if row[4] is not None else None,
                "category": str(row[5]) if row[5] is not None else None,
            })
        return result

    def calculate_board_breadth_snapshots(
        self, effective_date: date,
    ) -> dict[str, dict[str, object]]:
        trade_key = _date_key(effective_date)
        with self._lock:
            rows = self._connection.execute(
                """
                WITH memberships AS MATERIALIZED (
                    SELECT DISTINCT membership.board_instrument_id,
                                    stock.instrument_id AS stock_instrument_id
                    FROM board_memberships AS membership
                    JOIN instruments AS board
                      ON board.instrument_id = membership.board_instrument_id
                    JOIN instruments AS stock
                      ON stock.symbol = membership.member_symbol
                    WHERE membership.active = 1 AND board.active = 1
                      AND board.kind = 'sector' AND stock.kind = 'stock'
                ), stock_moves AS MATERIALIZED (
                    SELECT snapshot.instrument_id,
                           snapshot.change_percent / 100.0 AS return_1,
                           abs(snapshot.change_percent / 100.0)
                             * snapshot.close * snapshot.volume AS impact_proxy
                    FROM market_snapshots AS snapshot
                    JOIN instruments AS stock USING (instrument_id)
                    WHERE snapshot.trade_date = ? AND stock.kind = 'stock'
                      AND snapshot.close IS NOT NULL
                      AND snapshot.volume IS NOT NULL
                ), joined AS MATERIALIZED (
                    SELECT membership.board_instrument_id,
                           membership.stock_instrument_id, move.return_1,
                           move.impact_proxy
                    FROM memberships AS membership
                    LEFT JOIN stock_moves AS move
                      ON move.instrument_id = membership.stock_instrument_id
                )
                SELECT board.symbol, count(*), count(joined.return_1),
                       sum(CASE WHEN joined.return_1 > 0 THEN 1 ELSE 0 END),
                       sum(CASE WHEN joined.return_1 < 0 THEN 1 ELSE 0 END),
                       sum(CASE WHEN joined.return_1 = 0 THEN 1 ELSE 0 END),
                       avg(joined.return_1),
                       CASE WHEN sum(joined.impact_proxy) > 0 THEN
                           sum(joined.impact_proxy * joined.impact_proxy)
                           / (sum(joined.impact_proxy) * sum(joined.impact_proxy))
                       END
                FROM joined
                JOIN instruments AS board
                  ON board.instrument_id = joined.board_instrument_id
                GROUP BY joined.board_instrument_id, board.symbol
                """, (trade_key,),
            ).fetchall()
        return {str(row[0]): {
            "member_count": int(row[1]), "covered_member_count": int(row[2]),
            "coverage_ratio": round(int(row[2]) / int(row[1]), 6) if row[1] else 0.0,
            "advance_count": int(row[3]), "decline_count": int(row[4]),
            "unchanged_count": int(row[5]),
            "breadth": (
                round((int(row[3]) - int(row[4])) / int(row[2]), 6)
                if row[2] else None
            ),
            "average_return_1": round(float(row[6]), 6) if row[6] is not None else None,
            "impact_concentration_hhi": (
                round(float(row[7]), 6) if row[7] is not None else None
            ),
            "concentration_method": "abs-return-close-volume-hhi-v1",
            "membership_semantics": "current-active-membership",
        } for row in rows}

    def _ensure_signal_observation_columns(self) -> None:
        with self._lock, self._writer_lock:
            columns = {
                str(row[1]) for row in self._connection.execute(
                    "PRAGMA table_info(board_daily_observations)"
                )
            }
            if "deep_analysis_run_id" not in columns:
                self._connection.execute(
                    "ALTER TABLE board_daily_observations ADD COLUMN deep_analysis_run_id TEXT"
                )
            if "deep_analysis_summary_json" not in columns:
                self._connection.execute(
                    "ALTER TABLE board_daily_observations ADD COLUMN "
                    "deep_analysis_summary_json TEXT NOT NULL DEFAULT '{}'"
                )
            if "conclusion_code" not in columns:
                self._connection.execute(
                    "ALTER TABLE board_daily_observations ADD COLUMN "
                    "conclusion_code TEXT NOT NULL DEFAULT 'data-unavailable'"
                )
            if "rendered_summary" not in columns:
                self._connection.execute(
                    "ALTER TABLE board_daily_observations ADD COLUMN "
                    "rendered_summary TEXT NOT NULL DEFAULT ''"
                )
            if "comparison_json" not in columns:
                self._connection.execute(
                    "ALTER TABLE board_daily_observations ADD COLUMN "
                    "comparison_json TEXT NOT NULL DEFAULT '{}'"
                )

    def upsert_stock_daily_limits(
        self, source: str, limits: Sequence[StockDailyLimit],
    ) -> int:
        if not limits:
            return 0
        for item in limits:
            item.validate()
        symbols = {item.symbol.upper() for item in limits}
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            instrument_ids = self._instrument_ids(symbols)
            missing = symbols - instrument_ids.keys()
            if missing:
                raise ValueError("stock daily limits reference unknown instruments: " + ", ".join(sorted(missing)))
            source_id = self._source_id(source)
            self._connection.executemany(
                """
                INSERT INTO stock_daily_limits(
                    instrument_id, trade_date, up_limit, down_limit,
                    source_id, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id, trade_date) DO UPDATE SET
                    up_limit = excluded.up_limit,
                    down_limit = excluded.down_limit,
                    source_id = excluded.source_id,
                    updated_at_ms = excluded.updated_at_ms
                """,
                ((instrument_ids[item.symbol.upper()], _date_key(item.trade_date),
                  item.up_limit, item.down_limit, source_id, now_ms) for item in limits),
            )
        return len(limits)

    def has_stock_daily_limits(self, trade_date: date) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT count(*) FROM stock_daily_limits WHERE trade_date = ?",
                (_date_key(trade_date),),
            ).fetchone()
        return bool(row and int(row[0]) > 0)

    def calculate_market_emotion_snapshot(
        self, run_id: str, effective_date: date,
    ) -> dict[str, object]:
        trade_key = _date_key(effective_date)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT instrument.symbol, current.open, current.high, current.low,
                       current.close, previous.close, limits.up_limit,
                       limits.down_limit, limit_source.code
                FROM daily_bars AS current
                JOIN instruments AS instrument USING (instrument_id)
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
                LEFT JOIN sources AS limit_source ON limit_source.source_id = limits.source_id
                WHERE instrument.kind = 'stock' AND instrument.active = 1
                  AND current.trade_date = ?
                """, (trade_key,),
            ).fetchall()
        total = len(rows)
        comparable = sum(row[5] is not None for row in rows)
        limit_covered = sum(row[6] is not None and row[7] is not None for row in rows)
        tolerance = .005
        advances = declines = unchanged = 0
        limit_up = limit_down = broken_up = 0
        sources: set[str] = set()
        digest_rows = []
        for row in rows:
            close = float(row[4])
            previous = float(row[5]) if row[5] is not None else None
            up_limit = float(row[6]) if row[6] is not None else None
            down_limit = float(row[7]) if row[7] is not None else None
            if previous is not None:
                if close > previous + tolerance:
                    advances += 1
                elif close < previous - tolerance:
                    declines += 1
                else:
                    unchanged += 1
            if up_limit is not None and down_limit is not None:
                sources.add(str(row[8]))
                sealed_up = close >= up_limit - tolerance
                touched_up = float(row[2]) >= up_limit - tolerance
                if sealed_up:
                    limit_up += 1
                elif touched_up:
                    broken_up += 1
                if close <= down_limit + tolerance:
                    limit_down += 1
            digest_rows.append([
                str(row[0]), row[1], row[2], row[3], row[4], row[5], row[6], row[7],
            ])
        breadth_denominator = advances + declines
        sealing_denominator = limit_up + broken_up
        limit_metrics_available = limit_covered > 0
        status = (
            "unavailable" if total == 0 else
            "complete" if comparable / total >= .95 and limit_covered / total >= .90 else
            "partial"
        )
        metrics = {
            "advance_count": advances, "decline_count": declines,
            "unchanged_count": unchanged,
            "breadth": (
                round((advances - declines) / breadth_denominator, 6)
                if breadth_denominator else None
            ),
            "limit_up_count": limit_up if limit_metrics_available else None,
            "limit_down_count": limit_down if limit_metrics_available else None,
            "broken_up_count": broken_up if limit_metrics_available else None,
            "sealing_rate": (
                round(limit_up / sealing_denominator, 6)
                if limit_metrics_available and sealing_denominator else None
            ),
            "limit_balance": (
                round((limit_up - limit_down) / (limit_up + limit_down + 1), 6)
                if limit_metrics_available else None
            ),
        }
        coverage = {
            "active_stock_bar_count": total, "comparable_count": comparable,
            "limit_price_count": limit_covered,
            "comparable_ratio": round(comparable / total, 6) if total else 0,
            "limit_price_ratio": round(limit_covered / total, 6) if total else 0,
        }
        input_digest = __import__("hashlib").sha256(_json(digest_rows).encode("utf-8")).hexdigest()
        snapshot = {
            "run_id": run_id, "effective_date": effective_date.isoformat(), "status": status,
            "metrics": metrics, "sources": sorted(sources), "coverage": coverage,
            "algorithm_version": "market-emotion-snapshot-v1",
            "input_digest": input_digest,
        }
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            run_status = self._connection.execute(
                "SELECT status FROM signal_review_runs WHERE run_id = ?", (run_id,),
            ).fetchone()
            if run_status is None or str(run_status[0]) != "running":
                raise ValueError("signal review run is not running")
            self._connection.execute(
                """
                INSERT INTO market_emotion_snapshots(
                    run_id, effective_date, status, metrics_json, source_json,
                    coverage_json, algorithm_version, input_digest, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, trade_key, status, _json(metrics), _json(sorted(sources)),
                 _json(coverage), "market-emotion-snapshot-v1", input_digest, now_ms),
            )
        return snapshot

    def get_market_emotion_snapshot(self, run_id: str) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT run_id, effective_date, status, metrics_json, source_json,
                          coverage_json, algorithm_version, input_digest, created_at_ms
                   FROM market_emotion_snapshots WHERE run_id = ?""", (run_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "run_id": str(row[0]), "effective_date": _date_from_key(int(row[1])),
            "status": str(row[2]), "metrics": json.loads(str(row[3])),
            "sources": json.loads(str(row[4])), "coverage": json.loads(str(row[5])),
            "algorithm_version": str(row[6]), "input_digest": str(row[7]),
            "created_at_ms": int(row[8]),
        }

    def get_recent_daily_bars_many(
        self, symbols: Sequence[str], end_date: date, limit: int,
    ) -> dict[str, list[StoredDailyBar]]:
        if limit <= 0:
            raise ValueError("daily bar limit must be positive")
        ordered = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
        if len(ordered) > 200:
            raise ValueError("bulk daily bar query exceeds 200 symbols")
        if not ordered:
            return {}
        placeholders = ",".join("?" for _ in ordered)
        with self._lock:
            rows = self._connection.execute(
                f"""
                WITH ranked AS (
                    SELECT instrument.symbol, bar.trade_date, bar.open, bar.high,
                           bar.low, bar.close, bar.volume, source.code,
                           bar.updated_at_ms,
                           row_number() OVER (
                               PARTITION BY bar.instrument_id
                               ORDER BY bar.trade_date DESC
                           ) AS position
                    FROM daily_bars AS bar
                    JOIN instruments AS instrument USING (instrument_id)
                    JOIN sources AS source USING (source_id)
                    WHERE instrument.symbol IN ({placeholders})
                      AND bar.trade_date <= ?
                )
                SELECT symbol, trade_date, open, high, low, close, volume,
                       code, updated_at_ms
                FROM ranked WHERE position <= ?
                ORDER BY symbol, trade_date
                """,
                (*ordered, _date_key(end_date), limit),
            ).fetchall()
        result: dict[str, list[StoredDailyBar]] = {symbol: [] for symbol in ordered}
        for row in rows:
            symbol = str(row[0])
            result.setdefault(symbol, []).append(StoredDailyBar(
                symbol=symbol, trade_date=_date_from_key(int(row[1])),
                open=float(row[2]), high=float(row[3]), low=float(row[4]),
                close=float(row[5]), volume=int(row[6]), source=str(row[7]),
                updated_at_ms=int(row[8]),
            ))
        return result

    def save_board_daily_observations(
        self, run_id: str, observations: Sequence[dict[str, object]],
    ) -> int:
        if not observations:
            return 0
        symbols = {str(item["symbol"]).upper() for item in observations}
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            instrument_ids = self._instrument_ids(symbols)
            missing = symbols - instrument_ids.keys()
            if missing:
                raise ValueError("unknown board observations: " + ", ".join(sorted(missing)))
            status = self._connection.execute(
                "SELECT status FROM signal_review_runs WHERE run_id = ?", (run_id,),
            ).fetchone()
            if status is None or str(status[0]) != "running":
                raise ValueError("signal review run is not running")
            self._connection.executemany(
                """
                INSERT INTO board_daily_observations(
                    run_id, instrument_id, effective_date, coverage_state,
                    state_codes_json, metrics_json, disqualifiers_json,
                    attention_reasons_json, attention_eligible,
                    conclusion_code, rendered_summary, comparison_json,
                    deep_analysis_state, input_digest, algorithm_version,
                    config_version, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ((
                    run_id, instrument_ids[str(item["symbol"]).upper()],
                    _date_key(item["effective_date"]), item["coverage_state"],
                    _json(item.get("state_codes", [])), _json(item.get("metrics", {})),
                    _json(item.get("disqualifiers", [])),
                    _json(item.get("attention_reasons", [])),
                    int(bool(item.get("attention_eligible"))),
                    item.get("conclusion_code", "data-unavailable"),
                    item.get("rendered_summary", ""),
                    _json(item.get("comparison", {})),
                    item.get("deep_analysis_state", "not-requested"),
                    item["input_digest"], item["algorithm_version"],
                    item["config_version"], now_ms,
                ) for item in observations),
            )
        return len(observations)

    def list_board_daily_observations(
        self, *, run_id: str | None = None, symbol: str | None = None,
        query: str | None = None, attention_only: bool = False,
        limit: int = 200, offset: int = 0,
    ) -> list[dict[str, object]]:
        if not 1 <= limit <= 5000:
            raise ValueError("observation limit must be between 1 and 5000")
        clauses: list[str] = []
        parameters: list[object] = []
        if run_id:
            clauses.append("observation.run_id = ?")
            parameters.append(run_id)
        if symbol:
            clauses.append("instrument.symbol = ? COLLATE NOCASE")
            parameters.append(symbol.upper())
        if query and query.strip():
            pattern = f"%{query.strip()}%"
            clauses.append("(instrument.symbol LIKE ? OR instrument.name LIKE ?)")
            parameters.extend((pattern.upper(), pattern))
        if attention_only:
            clauses.append("observation.attention_eligible = 1")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT observation.run_id, instrument.symbol, instrument.name,
                       instrument.exchange, observation.effective_date,
                       observation.coverage_state, observation.state_codes_json,
                       observation.metrics_json, observation.disqualifiers_json,
                       observation.attention_reasons_json,
                       observation.attention_eligible,
                       observation.conclusion_code,
                       observation.rendered_summary,
                       observation.comparison_json,
                       observation.deep_analysis_state,
                       observation.deep_analysis_run_id,
                       observation.deep_analysis_summary_json,
                       observation.input_digest, observation.algorithm_version,
                       observation.config_version, observation.created_at_ms
                FROM board_daily_observations AS observation
                JOIN instruments AS instrument USING (instrument_id)
                {where}
                ORDER BY observation.effective_date DESC, instrument.symbol
                LIMIT ? OFFSET ?
                """, (*parameters, limit, offset),
            ).fetchall()
        return [_observation_row(row) for row in rows]

    def update_board_daily_deep_analysis(
        self, run_id: str, symbol: str, *, state: str,
        source_run_id: str | None, summary: dict[str, object],
    ) -> None:
        with self._lock, self._transaction():
            cursor = self._connection.execute(
                """
                UPDATE board_daily_observations
                SET deep_analysis_state = ?, deep_analysis_run_id = ?,
                    deep_analysis_summary_json = ?
                WHERE run_id = ? AND instrument_id = (
                    SELECT instrument_id FROM instruments
                    WHERE symbol = ? COLLATE NOCASE
                )
                """, (state, source_run_id, _json(summary), run_id, symbol.upper()),
            )
            if cursor.rowcount != 1:
                raise ValueError("board daily observation was not found")

    def update_board_daily_structural_scenario(
        self, run_id: str, symbol: str, *, metrics: dict[str, object],
        conclusion_code: str, rendered_summary: str,
        comparison: dict[str, object],
    ) -> None:
        """Replace a running observation's coarse scenario with its linked M4 result."""
        with self._lock, self._transaction():
            cursor = self._connection.execute(
                """
                UPDATE board_daily_observations
                SET metrics_json = ?, conclusion_code = ?, rendered_summary = ?,
                    comparison_json = ?
                WHERE run_id = ? AND instrument_id = (
                    SELECT instrument_id FROM instruments
                    WHERE symbol = ? COLLATE NOCASE
                ) AND EXISTS (
                    SELECT 1 FROM signal_review_runs
                    WHERE run_id = ? AND status = 'running'
                )
                """,
                (
                    _json(metrics), conclusion_code, rendered_summary,
                    _json(comparison), run_id, symbol.upper(), run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("running board daily observation was not found")

    def count_board_daily_observations(self, run_id: str) -> int:
        with self._lock:
            return int(self._connection.execute(
                "SELECT count(*) FROM board_daily_observations WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0])

    def set_signal_attention(
        self, signal_id: str, symbol: str, *, manual_pinned: bool,
        effective_date: date, reasons: Sequence[str] = (),
    ) -> dict[str, object]:
        normalized = symbol.upper()
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            identity = self._canonical_instrument_identity(normalized)
            if identity is None:
                raise ValueError(f"unknown attention instrument: {symbol}")
            _, instrument_id = identity
            existing = self._connection.execute(
                "SELECT first_observed_date FROM signal_attention_registry "
                "WHERE signal_id = ? AND instrument_id = ?",
                (signal_id, instrument_id),
            ).fetchone()
            first_date = int(existing[0]) if existing else _date_key(effective_date)
            status = "manual-pinned" if manual_pinned else "inactive"
            self._connection.execute(
                """
                INSERT INTO signal_attention_registry(
                    signal_id, instrument_id, status, manual_pinned,
                    first_observed_date, last_observed_date, cooldown_through_date,
                    reasons_json, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
                ON CONFLICT(signal_id, instrument_id) DO UPDATE SET
                    status = excluded.status,
                    manual_pinned = excluded.manual_pinned,
                    last_observed_date = excluded.last_observed_date,
                    cooldown_through_date = NULL,
                    reasons_json = excluded.reasons_json,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (signal_id, instrument_id, status, int(manual_pinned), first_date,
                 _date_key(effective_date), _json(list(reasons)), now_ms),
            )
        return self.get_signal_attention(signal_id, normalized)  # type: ignore[return-value]

    def promote_signal_attention(
        self, signal_id: str, symbol: str, effective_date: date,
        reasons: Sequence[str],
    ) -> None:
        normalized = symbol.upper()
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            identity = self._canonical_instrument_identity(normalized)
            if identity is None:
                raise ValueError(f"unknown attention instrument: {symbol}")
            _, instrument_id = identity
            existing = self._connection.execute(
                "SELECT manual_pinned, first_observed_date FROM signal_attention_registry "
                "WHERE signal_id = ? AND instrument_id = ?",
                (signal_id, instrument_id),
            ).fetchone()
            manual = bool(existing[0]) if existing else False
            first_date = int(existing[1]) if existing else _date_key(effective_date)
            status = "manual-pinned" if manual else "auto-promoted"
            self._connection.execute(
                """
                INSERT INTO signal_attention_registry(
                    signal_id, instrument_id, status, manual_pinned,
                    first_observed_date, last_observed_date, cooldown_through_date,
                    reasons_json, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
                ON CONFLICT(signal_id, instrument_id) DO UPDATE SET
                    status = excluded.status,
                    last_observed_date = excluded.last_observed_date,
                    cooldown_through_date = NULL,
                    reasons_json = excluded.reasons_json,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (signal_id, instrument_id, status, int(manual), first_date,
                 _date_key(effective_date), _json(list(reasons)), now_ms),
            )

    def advance_signal_attention_lifecycle(
        self, signal_id: str, symbol: str, effective_date: date,
        cooldown_through: date,
    ) -> dict[str, object] | None:
        normalized = symbol.upper()
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            row = self._connection.execute(
                """
                SELECT registry.instrument_id, registry.status,
                       registry.manual_pinned, registry.cooldown_through_date
                FROM signal_attention_registry AS registry
                JOIN instruments AS instrument USING (instrument_id)
                WHERE registry.signal_id = ? AND instrument.symbol = ? COLLATE NOCASE
                """, (signal_id, normalized),
            ).fetchone()
            if row is None or bool(row[2]):
                return self.get_signal_attention(signal_id, normalized)
            status = str(row[1])
            if status == "auto-promoted":
                next_status = "cooldown"
                next_cooldown = _date_key(cooldown_through)
                reasons = ["automatic-signal-resolved", "cooldown-observation"]
            elif status == "cooldown" and (
                row[3] is not None and _date_key(effective_date) >= int(row[3])
            ):
                next_status = "inactive"
                next_cooldown = int(row[3])
                reasons = ["cooldown-completed"]
            else:
                return self.get_signal_attention(signal_id, normalized)
            self._connection.execute(
                """
                UPDATE signal_attention_registry
                SET status = ?, last_observed_date = ?, cooldown_through_date = ?,
                    reasons_json = ?, updated_at_ms = ?
                WHERE signal_id = ? AND instrument_id = ?
                """,
                (next_status, _date_key(effective_date), next_cooldown,
                 _json(reasons), now_ms, signal_id, int(row[0])),
            )
        return self.get_signal_attention(signal_id, normalized)

    def list_signal_attention(
        self, signal_id: str, *, include_inactive: bool = False,
    ) -> list[dict[str, object]]:
        inactive = "" if include_inactive else "AND registry.status <> 'inactive'"
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT registry.signal_id, instrument.symbol, instrument.name,
                       instrument.exchange, registry.status, registry.manual_pinned,
                       registry.first_observed_date, registry.last_observed_date,
                       registry.cooldown_through_date, registry.reasons_json,
                       registry.updated_at_ms
                FROM signal_attention_registry AS registry
                JOIN instruments AS instrument USING (instrument_id)
                WHERE registry.signal_id = ? {inactive}
                ORDER BY registry.manual_pinned DESC, registry.last_observed_date DESC,
                         instrument.symbol
                """, (signal_id,),
            ).fetchall()
        return [_attention_row(row) for row in rows]

    def get_signal_attention(
        self, signal_id: str, symbol: str,
    ) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT registry.signal_id, instrument.symbol, instrument.name,
                       instrument.exchange, registry.status, registry.manual_pinned,
                       registry.first_observed_date, registry.last_observed_date,
                       registry.cooldown_through_date, registry.reasons_json,
                       registry.updated_at_ms
                FROM signal_attention_registry AS registry
                JOIN instruments AS instrument USING (instrument_id)
                WHERE registry.signal_id = ? AND instrument.symbol = ? COLLATE NOCASE
                """, (signal_id, symbol.upper()),
            ).fetchone()
        return _attention_row(row) if row else None


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _observation_row(row) -> dict[str, object]:
    return {
        "run_id": str(row[0]), "symbol": str(row[1]), "name": str(row[2]),
        "exchange": str(row[3]), "effective_date": _date_from_key(int(row[4])),
        "coverage_state": str(row[5]), "state_codes": json.loads(str(row[6])),
        "metrics": json.loads(str(row[7])),
        "disqualifiers": json.loads(str(row[8])),
        "attention_reasons": json.loads(str(row[9])),
        "attention_eligible": bool(row[10]), "conclusion_code": str(row[11]),
        "rendered_summary": str(row[12]), "comparison": json.loads(str(row[13])),
        "deep_analysis_state": str(row[14]), "deep_analysis_run_id": row[15],
        "deep_analysis_summary": json.loads(str(row[16])),
        "input_digest": str(row[17]), "algorithm_version": str(row[18]),
        "config_version": str(row[19]), "created_at_ms": int(row[20]),
    }


def _attention_row(row) -> dict[str, object]:
    return {
        "signal_id": str(row[0]), "symbol": str(row[1]), "name": str(row[2]),
        "exchange": str(row[3]), "status": str(row[4]),
        "manual_pinned": bool(row[5]),
        "first_observed_date": _date_from_key(int(row[6])),
        "last_observed_date": _date_from_key(int(row[7])),
        "cooldown_through_date": (
            _date_from_key(int(row[8])) if row[8] is not None else None
        ),
        "reasons": json.loads(str(row[9])), "updated_at_ms": int(row[10]),
    }
