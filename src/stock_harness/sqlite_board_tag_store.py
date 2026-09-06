"""Materialized stock industry/concept tag persistence."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timezone

from stock_harness.board_tagging import ALGORITHM_VERSION, BoardTagCandidate, select_board_tags
from stock_harness.sqlite_mapping import _instrument_classification


class SQLiteBoardTagStoreMixin:
    def list_instrument_board_tags(self, symbols: Sequence[str]) -> dict[str, list[dict[str, object]]]:
        ordered = list(dict.fromkeys(item.strip().upper() for item in symbols if item.strip()))
        result: dict[str, list[dict[str, object]]] = {symbol: [] for symbol in ordered}
        with self._lock:
            for offset in range(0, len(ordered), 400):
                chunk = ordered[offset : offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows = self._connection.execute(
                    f"""
                    SELECT stock.symbol, board.symbol, board.name, tag.classification,
                           tag.position, tag.source_system, tag.selection_score,
                           tag.selection_reason, tag.algorithm_version, tag.generated_at_ms
                    FROM instrument_board_tags AS tag
                    JOIN instruments AS stock ON stock.instrument_id = tag.instrument_id
                    JOIN instruments AS board ON board.instrument_id = tag.board_instrument_id
                    WHERE stock.symbol IN ({placeholders})
                    ORDER BY stock.symbol, tag.position
                    """,
                    chunk,
                ).fetchall()
                for row in rows:
                    result[str(row[0])].append({
                        "board_symbol": str(row[1]), "name": str(row[2]),
                        "classification": str(row[3]), "position": int(row[4]),
                        "source_system": str(row[5]), "selection_score": float(row[6]),
                        "selection_reason": str(row[7]), "algorithm_version": str(row[8]),
                        "generated_at_ms": int(row[9]),
                    })
        return result

    def rebuild_instrument_board_tags(self) -> dict[str, object]:
        with self._lock:
            rows = self._connection.execute(
                """
                WITH board_sizes AS (
                    SELECT board_instrument_id, COUNT(DISTINCT member_symbol) AS member_count
                    FROM board_memberships
                    WHERE active = 1
                    GROUP BY board_instrument_id
                )
                SELECT stock.instrument_id, stock.symbol, board.instrument_id,
                       board.symbol, board.name, board.exchange,
                       catalog.source_system, catalog.category, source.code,
                       board_sizes.member_count
                FROM board_memberships AS member
                JOIN instruments AS stock
                  ON stock.symbol = member.member_symbol AND stock.kind = 'stock'
                JOIN instruments AS board
                  ON board.instrument_id = member.board_instrument_id
                JOIN sources AS source USING (source_id)
                JOIN board_sizes USING (board_instrument_id)
                LEFT JOIN instrument_catalog_entries AS catalog
                  ON catalog.instrument_id = board.instrument_id
                 AND catalog.catalog_source_id = member.source_id
                WHERE member.active = 1 AND stock.active = 1
                ORDER BY stock.instrument_id, board.instrument_id, source.code
                """
            ).fetchall()

        candidates: dict[tuple[int, str], list[BoardTagCandidate]] = defaultdict(list)
        for row in rows:
            classification = _instrument_classification(
                "sector", str(row[5]), row[6], row[7]
            )
            if classification not in {"industry", "concept"}:
                continue
            candidates[(int(row[0]), str(row[1]))].append(BoardTagCandidate(
                board_id=int(row[2]), symbol=str(row[3]), name=str(row[4]),
                classification=classification,
                source_system=str(row[6] or row[8]), exchange=str(row[5]),
                member_count=int(row[9]),
            ))

        generated_at_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        inserts: list[tuple[object, ...]] = []
        industry_count = 0
        concept_count = 0
        for (instrument_id, _), items in candidates.items():
            for selected in select_board_tags(items):
                candidate = selected.candidate
                inserts.append((
                    instrument_id, candidate.board_id, candidate.classification,
                    selected.position, candidate.source_system, selected.score,
                    selected.reason, ALGORITHM_VERSION, generated_at_ms,
                ))
                if candidate.classification == "industry":
                    industry_count += 1
                else:
                    concept_count += 1

        with self._lock, self._transaction():
            self._connection.execute("DELETE FROM instrument_board_tags")
            self._connection.executemany(
                """
                INSERT INTO instrument_board_tags(
                    instrument_id, board_instrument_id, classification, position,
                    source_system, selection_score, selection_reason,
                    algorithm_version, generated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                inserts,
            )
        return {
            "algorithm_version": ALGORITHM_VERSION,
            "stock_count": len(candidates),
            "tagged_stock_count": len({int(item[0]) for item in inserts}),
            "tag_count": len(inserts),
            "industry_tag_count": industry_count,
            "concept_tag_count": concept_count,
            "generated_at_ms": generated_at_ms,
        }
