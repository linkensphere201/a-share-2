"""Build the persisted board observation pool from review-system outputs."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from stock_harness.sqlite_store import SQLiteMarketDataStore


WEEKLY_RECOGNITION_SIGNAL = "weekly-board-recognition"
BOARD_POOL_VERSION = "board-observation-pool-v3-leading-radar"


def _build_board_pool_snapshot(
    store: SQLiteMarketDataStore,
    run_id: str,
    effective_date: date,
    trend_scores: list[dict[str, object]],
    attention: list[dict[str, object]],
    prior_pool: dict[str, object] | None,
    hotspot_scores: list[dict[str, object]] | None = None,
    leading_scores: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    candidates: dict[str, dict[str, object]] = {}

    def candidate(symbol: str) -> dict[str, object]:
        return candidates.setdefault(symbol, {
            "symbol": symbol, "sources": [], "score": None,
            "hotspot_score": None,
            "leading_score": None,
            "attention": None,
        })

    eligible = [score for score in trend_scores if bool(score.get("eligible"))][:20]
    for score in trend_scores:
        symbol = str(score["symbol"])
        active_events = [
            event for event in score.get("hard_events", [])
            if isinstance(event, dict) and event.get("state") != "resolved"
        ]
        if score not in eligible and not active_events:
            continue
        value = candidate(symbol)
        value["score"] = score
        if score in eligible:
            value["sources"].append(_pool_source(
                "trend-score", run_id, str(score["entity_key"]),
                "eligible-top-20", {"rank": score["rank"],
                                    "total_score": score["total_score"]},
            ))
        for event in active_events:
            value["sources"].append(_pool_source(
                "hard-anomaly", run_id, str(score["entity_key"]),
                str(event["event_type"]), event,
            ))
    for score in hotspot_scores or []:
        if not bool(score.get("eligible")):
            continue
        symbol = str(score["symbol"])
        value = candidate(symbol)
        value["hotspot_score"] = score
        value["sources"].append(_pool_source(
            "hotspot-emergence", run_id, str(score["entity_key"]),
            str(score.get("hotspot_stage") or "hotspot-candidate"), {
                "rank": score["rank"],
                "total_score": score["total_score"],
                "stage": score.get("hotspot_stage"),
                "direction": score.get("score_direction"),
                "candidate_streak": score.get("candidate_streak"),
                "window_state": score.get("hotspot_window_state"),
                "window_qualified_sessions": score.get(
                    "hotspot_window_qualified_sessions"
                ),
            },
        ))
    for score in leading_scores or []:
        if not bool(score.get("leading_visible")):
            continue
        symbol = str(score["symbol"])
        value = candidate(symbol)
        value["leading_score"] = score
        value["sources"].append(_pool_source(
            "hotspot-leading", run_id, str(score["entity_key"]),
            str(score.get("leading_state") or "leading-candidate"), {
                "rank": score.get("leading_rank"),
                "total_score": score["total_score"],
                "state": score.get("leading_state"),
                "acceleration_count": score.get("leading_acceleration_count"),
                "dimensions": score.get("leading_dimensions"),
            },
        ))
    for entry in attention:
        symbol = str(entry["symbol"])
        value = candidate(symbol)
        value["attention"] = entry
        value["sources"].append(_pool_source(
            "attention-registry", run_id, symbol, str(entry["status"]),
            {"manual_pinned": entry["manual_pinned"],
             "reasons": entry["reasons"]},
        ))

    for board_symbol, assignments in _recognition_by_board(
        store, effective_date, set(candidates),
    ).items():
        value = candidate(board_symbol)
        value["sources"].extend(assignments)

    prior_items = {
        str(item["symbol"]): item
        for item in (prior_pool or {}).get("items", [])
        if isinstance(item, dict)
    }
    ordered = sorted(candidates.values(), key=lambda value: (
        not bool((value.get("attention") or {}).get("manual_pinned")),
        -max(
            float((value.get("score") or {}).get("total_score", -1)),
            float((value.get("hotspot_score") or {}).get("total_score", -1)),
            float((value.get("leading_score") or {}).get("total_score", -1)),
        ),
        str(value["symbol"]),
    ))
    items = []
    for rank, value in enumerate(ordered, 1):
        symbol = str(value["symbol"])
        attention_entry = value.get("attention") or {}
        score = value.get("score") or {}
        hotspot_score = value.get("hotspot_score") or {}
        leading_score = value.get("leading_score") or {}
        if attention_entry.get("manual_pinned"):
            lifecycle = "manual-pinned"
        elif attention_entry.get("status") == "cooldown":
            lifecycle = "cooldown"
        elif symbol not in prior_items:
            lifecycle = "new"
        elif (score.get("comparison") or {}).get("state") in {"strengthened", "weakened"}:
            lifecycle = str(score["comparison"]["state"])
        elif hotspot_score.get("score_direction") == "strengthening":
            lifecycle = "strengthened"
        elif hotspot_score.get("score_direction") == "declining":
            lifecycle = "weakened"
        elif leading_score.get("score_direction") == "strengthening":
            lifecycle = "strengthened"
        elif leading_score.get("score_direction") == "declining":
            lifecycle = "weakened"
        else:
            lifecycle = "active"
        items.append({
            "symbol": symbol, "lifecycle_state": lifecycle, "rank": rank,
            "payload": {
                "trend_score": score.get("total_score"),
                "trend_grade": score.get("grade"),
                "trend_eligible": bool(score.get("eligible")),
                "hotspot_score": hotspot_score.get("total_score"),
                "hotspot_grade": hotspot_score.get("grade"),
                "hotspot_eligible": bool(hotspot_score.get("eligible")),
                "hotspot_stage": hotspot_score.get("hotspot_stage"),
                "hotspot_direction": hotspot_score.get("score_direction"),
                "hotspot_peak_score": hotspot_score.get("peak_score"),
                "hotspot_window_state": hotspot_score.get(
                    "hotspot_window_state"
                ),
                "leading_score": leading_score.get("total_score"),
                "leading_grade": leading_score.get("grade"),
                "leading_eligible": bool(leading_score.get("eligible")),
                "leading_state": leading_score.get("leading_state"),
                "recognition_assignment_count": sum(
                    source["source_type"] == "recognition-assignment"
                    for source in value["sources"]
                ),
            },
            "sources": value["sources"],
        })
    return {
        "pool_kind": "board", "effective_date": effective_date,
        "algorithm_version": BOARD_POOL_VERSION,
        "summary": {
            "item_count": len(items), "eligible_score_limit": 20,
            "hotspot_admission_count": sum(
                bool((item["payload"]).get("hotspot_eligible")) for item in items
            ),
            "leading_admission_count": sum(
                bool((item["payload"]).get("leading_eligible")) for item in items
            ),
            "source_signal_run_id": run_id,
        },
        "items": items,
    }


def _recognition_by_board(
    store: SQLiteMarketDataStore, effective_date: date, board_symbols: set[str],
) -> dict[str, list[dict[str, object]]]:
    recognition_run = store.get_latest_succeeded_signal_review_run(
        WEEKLY_RECOGNITION_SIGNAL, effective_date,
    )
    if recognition_run is None:
        return {}
    result: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in store.list_signal_review_items(str(recognition_run["run_id"])):
        if not item["active"]:
            continue
        for evidence in item["evidence"]:
            if evidence["evidence_type"] != "board-recognition-ranking":
                continue
            payload = evidence["payload"]
            board_symbol = str(payload.get("board_symbol") or "")
            if board_symbol not in board_symbols:
                continue
            recognition_rank = int(payload.get("rank") or 0)
            result[board_symbol].append(_pool_source(
                "recognition-assignment", str(recognition_run["run_id"]),
                str(item["item_key"]), str(item["profile"]), {
                    "member_symbol": item["symbol"], "member_name": item["name"],
                    "rank": recognition_rank,
                    "recognition_role": f"{item['profile']}-rank-{recognition_rank}",
                    "score": payload.get("score"),
                    "confidence": payload.get("confidence"),
                    "board_name": payload.get("board_name"),
                },
            ))
    return result


def _pool_source(
    source_type: str, source_reference: str, source_entity_key: str,
    reason: str, payload: object,
) -> dict[str, object]:
    return {
        "source_type": source_type, "source_reference": source_reference,
        "source_entity_key": source_entity_key, "reason": reason,
        "payload": payload,
    }
