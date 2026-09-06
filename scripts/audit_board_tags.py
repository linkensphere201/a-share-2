"""Audit a materialized stock board-tag projection without changing it."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/market.sqlite"))
    parser.add_argument("--symbol", action="append", default=[])
    args = parser.parse_args()
    connection = sqlite3.connect(args.database)
    connection.row_factory = sqlite3.Row
    counts = connection.execute(
        """
        SELECT COUNT(*) AS tagged_stock_count, MAX(tag_count) AS max_tags,
               SUM(tag_count = 3) AS three_tag_stock_count
        FROM (
            SELECT instrument_id, COUNT(*) AS tag_count
            FROM instrument_board_tags GROUP BY instrument_id
        )
        """
    ).fetchone()
    active_stocks = connection.execute(
        "SELECT COUNT(*) FROM instruments WHERE kind = 'stock' AND active = 1"
    ).fetchone()[0]
    invalid_quotas = connection.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT instrument_id,
                   SUM(classification = 'industry') AS industries,
                   SUM(classification = 'concept') AS concepts,
                   COUNT(*) AS total
            FROM instrument_board_tags GROUP BY instrument_id
            HAVING industries > 1 OR concepts > 2 OR total > 3
        )
        """
    ).fetchone()[0]
    untagged = [str(row[0]) for row in connection.execute(
        """
        SELECT stock.symbol
        FROM instruments AS stock
        LEFT JOIN instrument_board_tags AS tag
          ON tag.instrument_id = stock.instrument_id
        WHERE stock.kind = 'stock' AND stock.active = 1
        GROUP BY stock.instrument_id
        HAVING COUNT(tag.board_instrument_id) = 0
        ORDER BY stock.symbol LIMIT 100
        """
    )]
    samples = {}
    for symbol in args.symbol:
        samples[symbol.upper()] = [dict(row) for row in connection.execute(
            """
            SELECT board.symbol AS board_symbol, board.name, tag.classification,
                   tag.position, tag.source_system, tag.selection_reason
            FROM instrument_board_tags AS tag
            JOIN instruments AS stock ON stock.instrument_id = tag.instrument_id
            JOIN instruments AS board ON board.instrument_id = tag.board_instrument_id
            WHERE stock.symbol = ? COLLATE NOCASE ORDER BY tag.position
            """,
            (symbol,),
        )]
    print(json.dumps({
        **dict(counts), "active_stock_count": active_stocks,
        "untagged_active_stock_count": active_stocks - int(counts["tagged_stock_count"] or 0),
        "untagged_active_symbols": untagged,
        "invalid_quota_count": invalid_quotas, "samples": samples,
    }, ensure_ascii=False, indent=2))
    connection.close()


if __name__ == "__main__":
    main()
