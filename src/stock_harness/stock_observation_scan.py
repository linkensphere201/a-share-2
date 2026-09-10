"""Bounded board-member and full-market lightweight stock scans."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Protocol

from stock_harness.board_leader_scan import is_risk_name
from stock_harness.models import StoredDailyBar
from stock_harness.stock_relative_strength import (
    LOOKBACK_BARS,
    analyze_relative_strength,
    score_relative_strength,
)


BOARD_MEMBER_SCAN_VERSION = "board-member-lightweight-scan-v2"
INDEPENDENT_SCAN_VERSION = "full-market-independent-scan-v2"
UNIFIED_STOCK_POOL_VERSION = "unified-stock-observation-pool-v4"
BATCH_SIZE = 200


class StockScanStore(Protocol):
    def get_recent_daily_bars_many(
        self, symbols: Sequence[str], end_date: date, limit: int,
    ) -> dict[str, list[StoredDailyBar]]: ...

    def get_recent_causally_adjusted_stock_bars_many(
        self, symbols: Sequence[str], end_date: date, limit: int,
    ) -> tuple[dict[str, list[StoredDailyBar]], dict[str, str]]: ...

    def list_board_members(
        self, board_symbol: str, limit: int = 500, offset: int = 0,
    ) -> list[dict[str, object]]: ...

    def list_active_stock_symbols_for_screening(
        self, as_of_date: date | None = None,
    ) -> list[dict[str, str]]: ...

    def list_stock_board_memberships_many(
        self, symbols: Sequence[str], board_limit: int = 3,
    ) -> dict[str, list[dict[str, object]]]: ...

    def list_all_stock_board_memberships(
        self, board_limit: int = 3,
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
    names: dict[str, str] = {}
    for board in board_items:
        board_symbol = str(board["symbol"])
        for member in store.list_board_members(board_symbol, limit=5000):
            if member.get("kind") != "stock" or not member.get("available"):
                continue
            symbol = str(member["symbol"])
            names[symbol] = str(member.get("name") or symbol)
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
        stock_bars, price_basis = store.get_recent_causally_adjusted_stock_bars_many(
            page, effective_date, LOOKBACK_BARS,
        )
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
            record["price_basis"] = price_basis.get(symbol, "raw")
            record["selection_qualified"] = record["price_basis"] in {
                "forward-adjusted-as-of", "raw-continuity-checked",
            }
            if not record["selection_qualified"]:
                record["eligible"] = False
                record.setdefault("disqualifiers", []).append("adjustment-factors-incomplete")
            record["name"] = names.get(symbol, symbol)
            record["risk_name"] = is_risk_name(str(record["name"]))
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
    seed_records: Sequence[Mapping[str, object]] = (),
) -> list[dict[str, object]]:
    """Scan active stocks causally in bounded batches without retaining all bars."""
    universe = store.list_active_stock_symbols_for_screening(effective_date)
    seeded = {
        str(item["symbol"]): dict(item) for item in seed_records
        if item.get("coverage_state") == "complete"
    }
    symbols = [str(item["symbol"]) for item in universe if str(item["symbol"]) not in seeded]
    exchange_by_symbol = {str(item["symbol"]): str(item.get("exchange") or "") for item in universe}
    name_by_symbol = {
        str(item["symbol"]): str(item.get("name") or item["symbol"])
        for item in universe
    }
    references = _load_reference_bars(store, effective_date)
    board_cache: dict[str, list[StoredDailyBar]] = {}
    list_all_memberships = getattr(store, "list_all_stock_board_memberships", None)
    membership_cache = (
        list_all_memberships(board_limit=3) if callable(list_all_memberships) else None
    )
    records: list[dict[str, object]] = list(seeded.values())
    if progress is not None:
        progress(len(symbols), 0)
    for offset in range(0, len(symbols), BATCH_SIZE):
        page = symbols[offset:offset + BATCH_SIZE]
        memberships = (
            {symbol: membership_cache.get(symbol, []) for symbol in page}
            if membership_cache is not None
            else store.list_stock_board_memberships_many(page, board_limit=3)
        )
        missing_boards = sorted({
            str(item["symbol"])
            for values in memberships.values() for item in values
            if str(item["symbol"]) not in board_cache
        })
        board_cache.update(_load_many(store, missing_boards, effective_date))
        stock_bars, price_basis = store.get_recent_causally_adjusted_stock_bars_many(
            page, effective_date, LOOKBACK_BARS,
        )
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
            record["price_basis"] = price_basis.get(symbol, "raw")
            record["selection_qualified"] = record["price_basis"] in {
                "forward-adjusted-as-of", "raw-continuity-checked",
            }
            if not record["selection_qualified"]:
                record["eligible"] = False
                record.setdefault("disqualifiers", []).append("adjustment-factors-incomplete")
            record["name"] = name_by_symbol.get(symbol, symbol)
            record["risk_name"] = is_risk_name(str(record["name"]))
            records.append(record)
        if progress is not None:
            progress(len(symbols), min(len(symbols), offset + len(page)))
    return score_relative_strength(records)


def build_unified_stock_pool_snapshot(
    source_run_id: str,
    effective_date: date,
    member_snapshot: Mapping[str, object],
    independent_records: Sequence[Mapping[str, object]],
    *,
    screener_run: Mapping[str, object] | None = None,
    screener_candidates: Sequence[Mapping[str, object]] = (),
    manual_attention: Sequence[Mapping[str, object]] = (),
    prior_snapshot: Mapping[str, object] | None = None,
) -> dict[str, object]:
    candidates: dict[str, dict[str, object]] = {}
    prior_items = {
        str(item["symbol"]): item for item in (prior_snapshot or {}).get("items", [])
        if isinstance(item, Mapping)
    }
    prior_focus_symbols = {
        symbol for symbol, item in prior_items.items()
        if isinstance(item.get("payload"), Mapping)
        and item["payload"].get("presentation_bucket") in {"focus", "opportunity"}
    }

    def candidate(symbol: str) -> dict[str, object]:
        return candidates.setdefault(symbol.upper(), {
            "symbol": symbol.upper(), "sources": [], "member": None,
            "independent": None, "screener": [], "manual": None,
        })

    for item in member_snapshot.get("items", []):
        if not isinstance(item, Mapping):
            continue
        payload = item.get("payload")
        if not isinstance(payload, Mapping):
            continue
        include = bool(payload.get("recognized") or payload.get("eligible")) or str(
            payload.get("classification")
        ) in {
            "independent-decline", "one-session-event-anomaly",
            "emerging-independent-move", "decaying-independent-move",
        }
        if not include:
            continue
        value = candidate(str(item["symbol"]))
        value["member"] = dict(payload)
        value["sources"].extend(
            dict(source) for source in item.get("sources", [])
            if isinstance(source, Mapping)
        )

    selected_independent = _select_independent_records(
        independent_records, preserve_symbols=prior_focus_symbols,
    )
    for record in selected_independent:
        symbol = str(record["symbol"])
        value = candidate(symbol)
        value["independent"] = dict(record)
        value["sources"].append({
            "source_type": "independent-strength",
            "source_reference": source_run_id,
            "source_entity_key": symbol,
            "reason": str(record.get("classification") or "independent-scan"),
            "payload": {
                "rank": record.get("rank"), "score": record.get("score"),
                "eligible": record.get("eligible"),
                "algorithm_version": INDEPENDENT_SCAN_VERSION,
            },
        })

    if screener_run is not None:
        screener_reference = str(screener_run["run_id"])
        for item in screener_candidates:
            symbol = str(item["symbol"])
            value = candidate(symbol)
            value["screener"].append(dict(item))
            value["sources"].append({
                "source_type": "screener-result",
                "source_reference": screener_reference,
                "source_entity_key": symbol,
                "reason": str(item.get("state") or screener_run.get("strategy_id")),
                "payload": {
                    "strategy_id": screener_run.get("strategy_id"),
                    "strategy_version": screener_run.get("strategy_version"),
                    "as_of_date": _iso(screener_run.get("as_of_date")),
                    "rank": item.get("rank"), "score": item.get("score"),
                    "analysis_run_id": item.get("analysis_run_id"),
                    "line_item_id": item.get("line_item_id"),
                },
            })

    for entry in manual_attention:
        if not bool(entry.get("manual_pinned")):
            continue
        symbol = str(entry["symbol"])
        value = candidate(symbol)
        value["manual"] = dict(entry)
        value["sources"].append({
            "source_type": "manual-pin",
            "source_reference": str(entry.get("signal_id") or "stock-observation-pool"),
            "source_entity_key": symbol,
            "reason": "manual-pinned",
            "payload": {
                "first_observed_date": _iso(entry.get("first_observed_date")),
                "last_observed_date": _iso(entry.get("last_observed_date")),
                "reasons": entry.get("reasons", []),
            },
        })

    ordered = sorted(candidates.values(), key=lambda value: (
        value["manual"] is None,
        not bool(value["screener"]),
        not bool((value["independent"] or value["member"] or {}).get("eligible")),
        not bool((value["member"] or {}).get("recognized")),
        -_independent_score(value),
        str(value["symbol"]),
    ))
    items = []
    for rank, value in enumerate(ordered, 1):
        symbol = str(value["symbol"])
        previous = prior_items.get(symbol)
        prior_payload = previous.get("payload") if isinstance(previous, Mapping) else None
        prior_payload = prior_payload if isinstance(prior_payload, Mapping) else {}
        was_focus = prior_payload.get("presentation_bucket") in {"focus", "opportunity"}
        payload = {
            "recognized": bool((value["member"] or {}).get("recognized")),
            "member_scan": value["member"],
            "independent_scan": value["independent"],
            "screener_results": value["screener"],
            "manual_pinned": value["manual"] is not None,
            "source_types": sorted({source["source_type"] for source in value["sources"]}),
            "independent_score": _independent_score(value),
            "risk_name": bool(
                (value["independent"] or value["member"] or {}).get("risk_name")
            ),
            "previous_presentation_bucket": prior_payload.get("presentation_bucket"),
            "focus_streak_sessions": (
                int(prior_payload.get("focus_streak_sessions") or 0) + 1
                if was_focus else 0
            ),
        }
        items.append({
            "symbol": symbol,
            "lifecycle_state": _merged_lifecycle(payload, previous),
            "rank": rank, "payload": payload,
            "sources": _deduplicate_sources(value["sources"]),
        })
    current_symbols = {str(item["symbol"]) for item in items}
    cooling = [
        item for symbol, item in prior_items.items()
        if symbol not in current_symbols and item.get("lifecycle_state") != "cooldown"
    ]
    for previous in sorted(cooling, key=lambda item: int(item.get("rank") or 0)):
        symbol = str(previous["symbol"])
        prior_payload = previous.get("payload")
        items.append({
            "symbol": symbol, "lifecycle_state": "cooldown", "rank": len(items) + 1,
            "payload": {
                **(dict(prior_payload) if isinstance(prior_payload, Mapping) else {}),
                "selected_current_run": False,
            },
            "sources": [{
                "source_type": "pool-lifecycle",
                "source_reference": str((prior_snapshot or {}).get("source_run_id") or "prior-pool"),
                "source_entity_key": symbol,
                "reason": "not-selected-current-run",
                "payload": {"prior_lifecycle_state": previous.get("lifecycle_state")},
            }],
        })
    return {
        "pool_kind": "stock", "effective_date": effective_date,
        "algorithm_version": UNIFIED_STOCK_POOL_VERSION,
        "summary": {
            "item_count": len(items), "cooldown_count": len(cooling),
            "board_derived_count": sum(item["member"] is not None for item in ordered),
            "independent_count": sum(item["independent"] is not None for item in ordered),
            "screener_count": sum(bool(item["screener"]) for item in ordered),
            "manual_count": sum(item["manual"] is not None for item in ordered),
            "source_signal_run_id": source_run_id,
        },
        "items": items,
    }


def _select_independent_records(
    records: Sequence[Mapping[str, object]], *,
    preserve_symbols: set[str] | frozenset[str] = frozenset(),
) -> list[Mapping[str, object]]:
    selected: dict[str, Mapping[str, object]] = {}
    eligible = [record for record in records if bool(record.get("eligible"))][:100]
    for record in eligible:
        selected[str(record["symbol"])] = record
    readiness = sorted(
        (record for record in records
         if record.get("selection_qualified", True) and _readiness_score(record) > 0),
        key=lambda record: (-_readiness_score(record), str(record["symbol"])),
    )[:100]
    for record in readiness:
        selected[str(record["symbol"])] = record
    for record in records:
        symbol = str(record["symbol"])
        if (
            symbol in preserve_symbols
            and record.get("selection_qualified", True)
            and not record.get("risk_name")
            and record.get("classification") not in {
                "independent-decline", "one-session-event-anomaly",
                "decaying-independent-move",
            }
        ):
            selected.setdefault(symbol, record)
    limits = {
        "independent-decline": 50,
        "one-session-event-anomaly": 50,
        "decaying-independent-move": 50,
    }
    for classification, limit in limits.items():
        matching = [
            record for record in records
            if record.get("classification") == classification
        ]
        if classification == "independent-decline":
            matching.sort(key=lambda record: _metric(record, "adjusted_residual", "20"))
        elif classification == "one-session-event-anomaly":
            matching.sort(
                key=lambda record: abs(_metric(record, "adjusted_residual", "1")),
                reverse=True,
            )
        else:
            matching.sort(key=lambda record: float(record.get("score") or 0), reverse=True)
        matching = matching[:limit]
        for record in matching:
            selected.setdefault(str(record["symbol"]), record)
    return list(selected.values())


def _readiness_score(record: Mapping[str, object]) -> float:
    metrics = record.get("metrics")
    if not isinstance(metrics, Mapping):
        return 0.0
    value = metrics.get("opportunity_readiness_score")
    return float(value) if isinstance(value, (int, float)) else 0.0


def _independent_score(value: Mapping[str, object]) -> float:
    scans = [value.get("independent"), value.get("member")]
    scan_scores = [
        float(scan.get("score") or 0) for scan in scans if isinstance(scan, Mapping)
    ]
    return max([*scan_scores, 0.0])


def _merged_lifecycle(
    payload: Mapping[str, object], previous: Mapping[str, object] | None,
) -> str:
    if payload.get("manual_pinned"):
        return "manual-pinned"
    independent = payload.get("independent_scan")
    member = payload.get("member_scan")
    classification = next((
        str(scan.get("classification")) for scan in (independent, member)
        if isinstance(scan, Mapping) and scan.get("classification")
    ), "")
    if (
        classification == "independent-decline"
        and not payload.get("screener_results")
    ):
        return "invalidated"
    if previous is None:
        return "new"
    prior_payload = previous.get("payload")
    prior_score = (
        float(prior_payload.get("independent_score") or 0)
        if isinstance(prior_payload, Mapping) else 0.0
    )
    score = float(payload.get("independent_score") or 0)
    if score >= prior_score + 8:
        return "strengthened"
    if score <= prior_score - 8:
        return "weakened"
    return "active"


def _deduplicate_sources(
    sources: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    result = []
    seen: set[tuple[str, str, str, str]] = set()
    for source in sources:
        key = (
            str(source["source_type"]), str(source["source_reference"]),
            str(source["source_entity_key"]), str(source["reason"]),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(source))
    return result


def _iso(value: object) -> str | None:
    return value.isoformat() if isinstance(value, date) else str(value) if value else None


def _metric(record: Mapping[str, object], group: str, key: str) -> float:
    metrics = record.get("metrics")
    values = metrics.get(group) if isinstance(metrics, Mapping) else None
    value = values.get(key) if isinstance(values, Mapping) else None
    return float(value) if isinstance(value, (int, float)) else 0.0


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
    previous_score = 0.0
    if isinstance(previous_payload, Mapping):
        previous_member = previous_payload.get("member_scan")
        previous_score = float(
            previous_payload.get("score")
            or previous_payload.get("independent_score")
            or (
                previous_member.get("score")
                if isinstance(previous_member, Mapping) else 0
            )
            or 0
        )
    score = float(record.get("score") or 0)
    if record.get("classification") in {"independent-decline", "data-unavailable"}:
        return "invalidated"
    if score >= previous_score + 8:
        return "strengthened"
    if score <= previous_score - 8:
        return "weakened"
    return "active"
