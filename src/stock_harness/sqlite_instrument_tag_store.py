"""Global instrument-level role tags, independent from custom-group membership."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone


MAX_INSTRUMENT_TAGS = 8
MAX_INSTRUMENT_TAG_LENGTH = 20


def normalize_instrument_tags(tags: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in tags:
        tag = str(value).strip()
        if not tag:
            continue
        if len(tag) > MAX_INSTRUMENT_TAG_LENGTH:
            raise ValueError(
                f"instrument tag exceeds {MAX_INSTRUMENT_TAG_LENGTH} characters"
            )
        folded = tag.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        normalized.append(tag)
    if len(normalized) > MAX_INSTRUMENT_TAGS:
        raise ValueError(f"at most {MAX_INSTRUMENT_TAGS} instrument tags are allowed")
    return normalized


class SQLiteInstrumentTagStoreMixin:
    def list_instrument_tags(
        self, symbols: Sequence[str]
    ) -> dict[str, list[str]]:
        ordered = list(dict.fromkeys(
            item.strip().upper() for item in symbols if item.strip()
        ))
        result = {symbol: [] for symbol in ordered}
        with self._lock:
            for offset in range(0, len(ordered), 400):
                chunk = ordered[offset : offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows = self._connection.execute(
                    f"""
                    SELECT instrument.symbol, tag.tag
                    FROM instrument_tags AS tag
                    JOIN instruments AS instrument USING (instrument_id)
                    WHERE instrument.symbol IN ({placeholders})
                    ORDER BY instrument.symbol, tag.position
                    """,
                    chunk,
                ).fetchall()
                for row in rows:
                    result[str(row[0])].append(str(row[1]))
        return result

    def replace_instrument_tags(
        self, symbol: str, tags: Sequence[str]
    ) -> list[str]:
        normalized_symbol = symbol.strip().upper()
        normalized_tags = normalize_instrument_tags(tags)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            row = self._connection.execute(
                "SELECT instrument_id, kind FROM instruments WHERE symbol = ? COLLATE NOCASE",
                (normalized_symbol,),
            ).fetchone()
            if row is None:
                raise ValueError("instrument not found")
            if str(row[1]) != "stock":
                raise ValueError("global instrument tags are only supported for stocks")
            instrument_id = int(row[0])
            self._connection.execute(
                "DELETE FROM instrument_tags WHERE instrument_id = ?", (instrument_id,)
            )
            self._connection.executemany(
                """
                INSERT INTO instrument_tags(instrument_id, tag, position, updated_at_ms)
                VALUES (?, ?, ?, ?)
                """,
                (
                    (instrument_id, tag, position, now_ms)
                    for position, tag in enumerate(normalized_tags)
                ),
            )
        return normalized_tags
