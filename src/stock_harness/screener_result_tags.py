"""Read-time tags for immutable screener snapshots; never rerun a strategy."""

from datetime import date

from stock_harness.sqlite_store import SQLiteMarketDataStore


def attach_recognition_tags(
    store: SQLiteMarketDataStore, candidates: list[dict[str, object]], as_of_date: str,
) -> list[dict[str, object]]:
    if not candidates:
        return []
    if all(isinstance(item.get("evidence", {}).get("recognition"), dict) for item in candidates):
        return [{**item, "recognition": item["evidence"]["recognition"]} for item in candidates]
    source = store.get_latest_succeeded_signal_review_run(
        "weekly-board-recognition", date.fromisoformat(as_of_date),
    )
    recognized: dict[str, set[str]] = {}
    if source:
        for item in store.list_signal_review_items(str(source["run_id"])):
            if item["active"] and item["profile"] in {"historical", "recent"}:
                recognized.setdefault(str(item["symbol"]), set()).add(item["profile"])
    return [{
        **item,
        "recognition": item.get("evidence", {}).get("recognition") or {
            "available": source is not None,
            "source_run_id": source["run_id"] if source else None,
            "source_date": source["effective_date"] if source else None,
            "tags": sorted(recognized.get(str(item["symbol"]), set())),
        },
    } for item in candidates]
