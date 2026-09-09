"""Immutable dated observation-pool snapshots and exact source provenance."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timezone
import json

from stock_harness.sqlite_mapping import _date_from_key, _date_key


class SQLiteObservationPoolStoreMixin:
    def _insert_observation_pool_snapshots(
        self, source_run_id: str, snapshots: Sequence[dict[str, object]],
    ) -> None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        for snapshot in snapshots:
            pool_kind = str(snapshot["pool_kind"])
            effective_date = snapshot["effective_date"]
            if not isinstance(effective_date, date):
                raise ValueError("observation pool effective_date must be a date")
            items = list(snapshot.get("items", []))
            symbols = {str(item["symbol"]).upper() for item in items}
            identities = self._instrument_ids(symbols)
            missing = symbols - identities.keys()
            if missing:
                raise ValueError("unknown pool instruments: " + ", ".join(sorted(missing)))
            self._connection.execute(
                """
                INSERT INTO observation_pool_snapshots(
                    source_run_id, pool_kind, effective_date, algorithm_version,
                    summary_json, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source_run_id, pool_kind, _date_key(effective_date),
                 snapshot["algorithm_version"], _json(snapshot.get("summary", {})),
                 now_ms),
            )
            for item in items:
                symbol = str(item["symbol"]).upper()
                instrument_id = identities[symbol]
                self._connection.execute(
                    """
                    INSERT INTO observation_pool_items(
                        source_run_id, pool_kind, instrument_id, lifecycle_state,
                        rank, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (source_run_id, pool_kind, instrument_id,
                     item["lifecycle_state"], item["rank"],
                     _json(item.get("payload", {}))),
                )
                for position, source in enumerate(item.get("sources", []), 1):
                    self._connection.execute(
                        """
                        INSERT INTO observation_pool_sources(
                            source_run_id, pool_kind, instrument_id, source_type,
                            source_reference, source_entity_key, reason,
                            payload_json, position
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (source_run_id, pool_kind, instrument_id,
                         source["source_type"], source["source_reference"],
                         source["source_entity_key"], source["reason"],
                         _json(source.get("payload", {})), position),
                    )

    def get_observation_pool_snapshot(
        self, source_run_id: str, pool_kind: str,
    ) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT effective_date, algorithm_version, summary_json, created_at_ms
                FROM observation_pool_snapshots
                WHERE source_run_id = ? AND pool_kind = ?
                """, (source_run_id, pool_kind),
            ).fetchone()
            if row is None:
                return None
            item_rows = self._connection.execute(
                """
                SELECT item.instrument_id, instrument.symbol, instrument.name,
                       instrument.kind, instrument.exchange, item.lifecycle_state,
                       item.rank, item.payload_json
                FROM observation_pool_items AS item
                JOIN instruments AS instrument USING (instrument_id)
                WHERE item.source_run_id = ? AND item.pool_kind = ?
                ORDER BY item.rank, instrument.symbol
                """, (source_run_id, pool_kind),
            ).fetchall()
            source_rows = self._connection.execute(
                """
                SELECT instrument_id, source_type, source_reference,
                       source_entity_key, reason, payload_json
                FROM observation_pool_sources
                WHERE source_run_id = ? AND pool_kind = ?
                ORDER BY instrument_id, position
                """, (source_run_id, pool_kind),
            ).fetchall()
        sources: dict[int, list[dict[str, object]]] = {}
        for value in source_rows:
            sources.setdefault(int(value[0]), []).append({
                "source_type": str(value[1]), "source_reference": str(value[2]),
                "source_entity_key": str(value[3]), "reason": str(value[4]),
                "payload": json.loads(str(value[5])),
            })
        return {
            "source_run_id": source_run_id, "pool_kind": pool_kind,
            "effective_date": _date_from_key(int(row[0])),
            "algorithm_version": str(row[1]),
            "summary": json.loads(str(row[2])), "created_at_ms": int(row[3]),
            "items": [{
                "symbol": str(item[1]), "name": str(item[2]),
                "kind": str(item[3]), "exchange": str(item[4]),
                "lifecycle_state": str(item[5]), "rank": int(item[6]),
                "payload": json.loads(str(item[7])),
                "sources": sources.get(int(item[0]), []),
            } for item in item_rows],
        }


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
