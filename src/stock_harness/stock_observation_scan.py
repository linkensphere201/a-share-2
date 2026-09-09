"""Bounded board-member and full-market lightweight stock scans."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Protocol

from stock_harness.models import StoredDailyBar
from stock_harness.stock_relative_strength import (
    LOOKBACK_BARS,
    analyze_relative_strength,
    score_relative_strength,
)


BOARD_MEMBER_SCAN_VERSION = "board-member-lightweight-scan-v1"
INDEPENDENT_SCAN_VERSION = "full-market-independent-scan-v1"
BATCH_SIZE = 200


class StockScanStore(Protocol):
    def get_recent_daily_bars_many(
        self, symbols: Sequence[str], end_date: date, limit: int,
    ) -> dict[str, list[StoredDailyBar]]: ...

    def list_board_members(
        self, board_symbol: str, limit: int = 500, offset: int = 0,
    ) -> list[dict[str, object]]: ...

    def list_active_stock_symbols_for_screening(self) -> list[dict[str, str]]: ...

    def list_stock_board_memberships_many(
        self, symbols: Sequence[str], board_limit: int = 3,
    ) -> dict[str, list[dict[str, object]]]: ...


def build_board_member_scan_snapshot(
    store: StockScanStore,
    source_run_id: str,
    effective_date: date,
    board_pool: Mapping[str, object],
    prior_snapshot: Mapping[str, object] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, object]:
    board_items = [
        item for item in board_pool.get("items", []) if isinstance(item, Mapping)
    ]
    board_rank = {
        str(item["symbol"]): int(item.get("rank") or index)
        for index, item in enumerate(board_items, 1)
    }
    memberships: dict[str, list[dict[str, object]]] = {}
    recognition: dict[str, list[dict[str, object]]] = {}
    for board in board_items:
        board_symbol = str(board["symbol"])
        for member in store.list_board_members(board_symbol, limit=5000):
            if member.get("kind") != "stock" or not member.get("available"):
                continue
            symbol = str(member["symbol"])
            memberships.setdefault(symbol, []).append({
                "board_symbol": board_symbol,
                "board_rank": board_rank[board_symbol],
                "membership_source": member.get("source"),
                "exchange": member.get("exchange"),
            })
        for source in board.get("sources", []):
            if not isinstance(source, Mapping) or source.get("source_type") != "recognition-assignment":
                continue
            payload = source.get("payload")
            if not isinstance(payload, Mapping) or not payload.get("member_symbol"):
                continue
            recognition.setdefault(str(payload["member_symbol"]), []).append(dict(source))
    ordered_symbols = sorted(
        memberships,
        key=lambda symbol: (
            symbol not in recognition,
            min(item["board_rank"] for item in memberships[symbol]),
            symbol,
        ),
    )
    references = _load_reference_bars(store, effective_date)
    board_symbols = sorted(board_rank, key=board_rank.get)
    board_bars = _load_many(store, board_symbols, effective_date)
    records: list[dict[str, object]] = []
    total = len(ordered_symbols)
    for offset in range(0, total, BATCH_SIZE):
        page = ordered_symbols[offset:offset + BATCH_SIZE]
        stock_bars = store.get_recent_daily_bars_many(page, effective_date, LOOKBACK_BARS)
        for symbol in page:
            associated = sorted(
                memberships[symbol], key=lambda item: (item["board_rank"], item["board_symbol"])
            )[:3]
            record = analyze_relative_strength(
                symbol, stock_bars.get(symbol, []), effective_date,
                market_references=_references_for_exchange(
                    references, str(associated[0].get("exchange") or "") if associated else "",
                ),
                board_references={
                    str(item["board_symbol"]): board_bars.get(str(item["board_symbol"]), [])
                    for item in associated
                },
            )
            record["associated_boards"] = associated
            record["recognized"] = symbol in recognition
            records.append(record)
        if progress is not None:
            progress(total, min(total, offset + len(page)))
    scored = score_relative_strength(records)
    incomplete = [
        record for record in records if record.get("coverage_state") != "complete"
    ]
    ordered_records = [*scored, *sorted(incomplete, key=lambda item: str(item["symbol"]))]
    prior_items = {
        str(item["symbol"]): item for item in (prior_snapshot or {}).get("items", [])
        if isinstance(item, Mapping)
    }
    items = []
    for rank, record in enumerate(ordered_records, 1):
        symbol = str(record["symbol"])
        previous = prior_items.get(symbol)
        lifecycle = _lifecycle(record, previous)
        sources = [{
            "source_type": "board-membership",
            "source_reference": source_run_id,
            "source_entity_key": str(link["board_symbol"]),
            "reason": "pooled-board-member",
            "payload": link,
        } for link in memberships[symbol]]
        sources.extend(recognition.get(symbol, []))
        items.append({
            "symbol": symbol, "lifecycle_state": lifecycle, "rank": rank,
            "payload": record, "sources": sources,
        })
    return {
        "pool_kind": "stock", "effective_date": effective_date,
        "algorithm_version": BOARD_MEMBER_SCAN_VERSION,
        "summary": {
            "item_count": len(items), "complete_count": len(scored),
            "eligible_count": sum(bool(item.get("eligible")) for item in scored),
            "recognized_count": len(recognition), "board_count": len(board_items),
            "source_signal_run_id": source_run_id,
        },
        "items": items,
    }


def scan_full_market_independent_strength(
    store: StockScanStore,
    effective_date: date,
    progress: Callable[[int, int], None] | None = None,
) -> list[dict[str, object]]:
    """Scan active stocks causally in bounded batches without retaining all bars."""
    universe = store.list_active_stock_symbols_for_screening()
    symbols = [str(item["symbol"]) for item in universe]
    exchange_by_symbol = {str(item["symbol"]): str(item.get("exchange") or "") for item in universe}
    references = _load_reference_bars(store, effective_date)
    board_cache: dict[str, list[StoredDailyBar]] = {}
    records: list[dict[str, object]] = []
    for offset in range(0, len(symbols), BATCH_SIZE):
        page = symbols[offset:offset + BATCH_SIZE]
        memberships = store.list_stock_board_memberships_many(page, board_limit=3)
        missing_boards = sorted({
            str(item["symbol"])
            for values in memberships.values() for item in values
            if str(item["symbol"]) not in board_cache
        })
        board_cache.update(_load_many(store, missing_boards, effective_date))
        stock_bars = store.get_recent_daily_bars_many(page, effective_date, LOOKBACK_BARS)
        for symbol in page:
            associated = memberships.get(symbol, [])
            record = analyze_relative_strength(
                symbol, stock_bars.get(symbol, []), effective_date,
                market_references=_references_for_exchange(
                    references, exchange_by_symbol.get(symbol, ""),
                ),
                board_references={
                    str(item["symbol"]): board_cache.get(str(item["symbol"]), [])
                    for item in associated
                },
            )
            record["associated_boards"] = associated
            records.append(record)
        if progress is not None:
            progress(len(symbols), min(len(symbols), offset + len(page)))
    return score_relative_strength(records)


def _load_reference_bars(
    store: StockScanStore, effective_date: date,
) -> dict[str, list[StoredDailyBar]]:
    candidates = ["000001.SH", "399001.SZ", "899050.BJ", "000985.CSI", "SHAMV.A"]
    return {
        symbol: bars for symbol, bars in _load_many(store, candidates, effective_date).items()
        if bars
    }


def _load_many(
    store: StockScanStore, symbols: Sequence[str], effective_date: date,
) -> dict[str, list[StoredDailyBar]]:
    result: dict[str, list[StoredDailyBar]] = {}
    for offset in range(0, len(symbols), BATCH_SIZE):
        page = symbols[offset:offset + BATCH_SIZE]
        result.update(store.get_recent_daily_bars_many(page, effective_date, LOOKBACK_BARS))
    return result


def _references_for_exchange(
    references: Mapping[str, list[StoredDailyBar]], exchange: str,
) -> dict[str, list[StoredDailyBar]]:
    exchange_symbol = {
        "SH": "000001.SH", "SZ": "399001.SZ", "BJ": "899050.BJ",
    }.get(exchange.upper())
    selected = ["000985.CSI", "SHAMV.A"]
    if exchange_symbol is not None:
        selected.insert(0, exchange_symbol)
    if not any(symbol in references for symbol in selected):
        selected.append("000001.SH")
    return {symbol: references[symbol] for symbol in selected if symbol in references}


def _lifecycle(
    record: Mapping[str, object], previous: Mapping[str, object] | None,
) -> str:
    if previous is None:
        return "new"
    previous_payload = previous.get("payload")
    previous_score = (
        float(previous_payload.get("score") or 0)
        if isinstance(previous_payload, Mapping) else 0.0
    )
    score = float(record.get("score") or 0)
    if record.get("classification") in {"independent-decline", "data-unavailable"}:
        return "invalidated"
    if score >= previous_score + 8:
        return "strengthened"
    if score <= previous_score - 8:
        return "weakened"
    return "active"
