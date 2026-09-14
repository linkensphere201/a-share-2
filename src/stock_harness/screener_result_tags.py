"""Read-time tags for immutable screener snapshots; never rerun a strategy."""

from datetime import date

from stock_harness.sqlite_store import SQLiteMarketDataStore


def attach_recognition_tags(
    store: SQLiteMarketDataStore, candidates: list[dict[str, object]], as_of_date: str,
) -> list[dict[str, object]]:
    if not candidates:
        return []
    source = store.get_latest_succeeded_signal_review_run(
        "weekly-board-recognition", date.fromisoformat(as_of_date),
    )
    recognized: set[str] = set()
    if source:
        recognized = {
            str(item["symbol"])
            for item in store.list_signal_review_items(str(source["run_id"]))
            if item["active"] and item["profile"] == "historical"
        }
    return [{
        **item,
        "recognition": {
            "available": source is not None,
            "source_run_id": source["run_id"] if source else None,
            "source_date": source["effective_date"] if source else None,
            "tags": ["historical"] if item["symbol"] in recognized else [],
        },
    } for item in candidates]
