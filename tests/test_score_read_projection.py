import json
import sqlite3
from threading import RLock

from stock_harness.sqlite_signal_review_store import SQLiteSignalReviewStoreMixin


def test_workspace_score_projection_filters_before_pagination_and_preserves_evidence():
    store = SQLiteSignalReviewStoreMixin()
    store._connection = sqlite3.connect(":memory:")
    store._lock = RLock()
    connection = store._connection
    connection.executescript("""
        CREATE TABLE instruments (instrument_id INTEGER, symbol TEXT, name TEXT, kind TEXT, exchange TEXT);
        CREATE TABLE signal_review_runs (run_id TEXT, effective_date INTEGER);
        CREATE TABLE signal_review_scores (
            run_id TEXT, instrument_id INTEGER, system_id TEXT, entity_scope TEXT,
            eligible INTEGER, rank INTEGER, payload_json TEXT);
        INSERT INTO signal_review_runs VALUES ('run', 20260914);
    """)
    for index, scope in enumerate(["stock", "board", "market", "stock", "board"]):
        payload = {"system_payload": {"raw": ["diagnostic"] * 100},
                   "entity_scope": scope, "summary": "unchanged",
                   "chart_projection": [{"price": 12.5}], "evidence": [{"id": "E1"}]}
        connection.execute("INSERT INTO instruments VALUES (?, ?, ?, ?, 'test')",
                           (index, str(index), str(index), scope))
        connection.execute("INSERT INTO signal_review_scores VALUES ('run', ?, 'mean-reversion', ?, 1, ?, ?)",
                           (index, scope, index + 1, json.dumps(payload)))
    try:
        full = store.list_signal_review_scores("run")
        assert len(full) == store.count_signal_review_scores("run") == 5
        for universe, symbols in [("boards", ["1", "2", "4"]), ("stocks", ["0", "3"])]:
            assert store.count_signal_review_scores("run", universe=universe) == len(symbols)
            compact = [store.list_signal_review_scores(
                "run", "mean-reversion", limit=1, offset=offset,
                include_system_payload=False, universe=universe,
            )[0] for offset in range(len(symbols))]
            assert [row["symbol"] for row in compact] == symbols
            assert compact == [{key: value for key, value in row.items() if key != "system_payload"}
                               for row in full if row["symbol"] in symbols]
    finally:
        connection.close()
