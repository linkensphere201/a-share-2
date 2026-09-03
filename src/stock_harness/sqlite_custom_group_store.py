"""Custom instrument group persistence for the SQLite market data store."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone

from stock_harness.search_terms import matches_name_or_pinyin


_CUSTOM_GROUP_ROLES = {
    "", "sentiment_anchor", "liquidity_anchor", "bellwether",
    "core_identity", "lagging_expansion",
}


def _custom_group_role(value: object) -> str:
    role = str(value).strip()
    if role not in _CUSTOM_GROUP_ROLES:
        raise ValueError(f"invalid custom group member role: {role}")
    return role


class SQLiteCustomGroupStoreMixin:
    def list_custom_groups(self, query: str = "") -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT custom.group_id, custom.name, custom.description,
                       custom.created_at_ms, custom.updated_at_ms, count(member.instrument_id)
                FROM custom_instrument_groups AS custom
                LEFT JOIN custom_instrument_group_members AS member USING (group_id)
                GROUP BY custom.group_id
                ORDER BY custom.name COLLATE NOCASE, custom.group_id
                """
            ).fetchall()
            member_rows = self._connection.execute(
                """
                SELECT member.group_id, instrument.symbol
                FROM custom_instrument_group_members AS member
                JOIN instruments AS instrument USING (instrument_id)
                ORDER BY member.group_id, member.position
                """
            ).fetchall()
        member_symbols = list(dict.fromkeys(str(row[1]) for row in member_rows))
        snapshot_rows: list[dict[str, object]] = []
        for offset in range(0, len(member_symbols), 500):
            snapshot_rows.extend(self.list_market_snapshots(
                member_symbols[offset : offset + 500]
            ))
        changes = {
            str(item["symbol"]): float(item["change_percent"])
            for item in snapshot_rows if item.get("change_percent") is not None
        }
        group_changes: dict[str, list[float]] = {}
        for row in member_rows:
            change = changes.get(str(row[1]))
            if change is not None:
                group_changes.setdefault(str(row[0]), []).append(change)
        groups = [
            {
                "id": str(row[0]), "symbol": f"CUSTOM:{row[0]}",
                "name": str(row[1]), "description": str(row[2]),
                "member_count": int(row[5]), "created_at_ms": int(row[3]),
                "updated_at_ms": int(row[4]),
                "average_change_percent": (
                    sum(group_changes[str(row[0])]) / len(group_changes[str(row[0])])
                    if group_changes.get(str(row[0])) else None
                ),
            }
            for row in rows
        ]
        return groups if not query.strip() else [
            item for item in groups
            if matches_name_or_pinyin(query, str(item["name"]), str(item["description"]))
        ]

    def get_custom_group(self, group_id: str) -> dict[str, object] | None:
        with self._lock:
            group = self._connection.execute(
                """
                SELECT group_id, name, description, created_at_ms, updated_at_ms
                FROM custom_instrument_groups WHERE group_id = ?
                """,
                (group_id,),
            ).fetchone()
            if group is None:
                return None
            rows = self._connection.execute(
                """
                SELECT instrument.symbol, instrument.name, instrument.kind,
                       instrument.exchange, member.position, member.role,
                       member.tags_json, member.note, instrument.active
                FROM custom_instrument_group_members AS member
                JOIN instruments AS instrument USING (instrument_id)
                WHERE member.group_id = ?
                ORDER BY member.position, instrument.symbol
                """,
                (group_id,),
            ).fetchall()
        return {
            "id": str(group[0]), "symbol": f"CUSTOM:{group[0]}",
            "name": str(group[1]), "description": str(group[2]),
            "created_at_ms": int(group[3]), "updated_at_ms": int(group[4]),
            "members": [
                {
                    "symbol": str(row[0]), "name": str(row[1]),
                    "kind": str(row[2]), "exchange": str(row[3]),
                    "position": int(row[4]), "role": str(row[5]),
                    "tags": json.loads(str(row[6])),
                    "note": str(row[7]), "available": bool(row[8]),
                }
                for row in rows
            ],
        }

    def create_custom_group(
        self,
        group_id: str,
        name: str,
        description: str = "",
        members: Sequence[dict[str, object]] = (),
    ) -> dict[str, object]:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("custom group name is required")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            self._connection.execute(
                """
                INSERT INTO custom_instrument_groups(
                    group_id, name, description, created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (group_id, normalized_name, description.strip(), now_ms, now_ms),
            )
            self._replace_custom_group_members_locked(group_id, members, now_ms)
        result = self.get_custom_group(group_id)
        assert result is not None
        return result

    def update_custom_group(
        self,
        group_id: str,
        name: str,
        description: str = "",
        members: Sequence[dict[str, object]] = (),
    ) -> dict[str, object] | None:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("custom group name is required")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            cursor = self._connection.execute(
                """
                UPDATE custom_instrument_groups
                SET name = ?, description = ?, updated_at_ms = ?
                WHERE group_id = ?
                """,
                (normalized_name, description.strip(), now_ms, group_id),
            )
            if cursor.rowcount == 0:
                return None
            self._replace_custom_group_members_locked(group_id, members, now_ms)
        return self.get_custom_group(group_id)

    def delete_custom_group(self, group_id: str) -> bool:
        with self._lock, self._transaction():
            self._connection.execute(
                "DELETE FROM custom_instrument_group_members WHERE group_id = ?",
                (group_id,),
            )
            cursor = self._connection.execute(
                "DELETE FROM custom_instrument_groups WHERE group_id = ?",
                (group_id,),
            )
        return cursor.rowcount > 0

    def _replace_custom_group_members_locked(
        self,
        group_id: str,
        members: Sequence[dict[str, object]],
        now_ms: int,
    ) -> None:
        symbols = [
            value if value.upper().startswith("FUT") else value.upper()
            for item in members
            if (value := str(item["symbol"]).strip())
        ]
        if len(symbols) != len(members):
            raise ValueError("custom group member symbol is required")
        if len(symbols) != len(set(symbols)):
            raise ValueError("custom group members must contain unique symbols")
        instrument_ids = self._instrument_ids(set(symbols))
        missing = set(symbols) - instrument_ids.keys()
        if missing:
            raise ValueError(f"unknown instruments: {', '.join(sorted(missing))}")
        product_symbols: list[str] = []
        instrument_id_values = list(instrument_ids.values())
        for offset in range(0, len(instrument_id_values), 400):
            chunk = instrument_id_values[offset : offset + 400]
            placeholders = ",".join("?" for _ in chunk)
            product_symbols.extend(str(row[0]) for row in self._connection.execute(
                f"SELECT symbol FROM instruments WHERE instrument_id IN ({placeholders}) "
                "AND kind = 'futures-product'",
                chunk,
            ).fetchall())
        if product_symbols:
            raise ValueError(
                "futures products are catalog nodes, not custom group members: "
                + ", ".join(sorted(product_symbols))
            )
        self._connection.execute(
            "DELETE FROM custom_instrument_group_members WHERE group_id = ?",
            (group_id,),
        )
        self._connection.executemany(
            """
            INSERT INTO custom_instrument_group_members(
                group_id, instrument_id, position, role, tags_json, note, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    group_id, instrument_ids[symbol], position,
                    _custom_group_role(item.get("role", "")),
                    json.dumps(
                        [str(tag).strip() for tag in item.get("tags", []) if str(tag).strip()],
                        ensure_ascii=False, separators=(",", ":"),
                    ),
                    str(item.get("note", "")).strip(), now_ms,

                )
                for position, (symbol, item) in enumerate(zip(symbols, members))
            ),
        )


