"""Manual signal-review execution with immutable, comparable snapshots."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import date, timedelta
import hashlib
import json
import logging
import threading
import time
from uuid import uuid4

from stock_harness.board_leader_scan import (
    ALGORITHM_VERSION,
    HISTORICAL_PROFILE,
    RECENT_PROFILE,
    calculate_stock_features,
    compact_returns,
    is_risk_name,
    rank_board_leaders,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore


LOGGER = logging.getLogger(__name__)
WEEKLY_RECOGNITION_SIGNAL = "weekly-board-recognition"
DEFINITION_VERSION = "weekly-board-recognition-v1"
HISTORICAL_LIMIT = 5


class SignalReviewBusyError(RuntimeError):
    pass


class SignalReviewService:
    def __init__(self, store: SQLiteMarketDataStore) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        recovered = store.recover_interrupted_signal_review_runs()
        if recovered:
            LOGGER.warning("signal_review_interrupted_runs_recovered count=%s", recovered)

    @staticmethod
    def definitions() -> list[dict[str, object]]:
        return [{
            "signal_id": WEEKLY_RECOGNITION_SIGNAL,
            "name": "板块辨识度周观察",
            "description": "重算近期与历史高权辨识度品种，并与上一轮结果比较。",
            "cadence": "weekly",
            "definition_version": DEFINITION_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "manual_only": True,
            "profiles": [RECENT_PROFILE, HISTORICAL_PROFILE],
        }]

    def start_run(
        self, signal_id: str, effective_date: date | None = None,
    ) -> dict[str, object]:
        if signal_id != WEEKLY_RECOGNITION_SIGNAL:
            raise ValueError(f"unknown signal: {signal_id}")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise SignalReviewBusyError("another signal review run is already active")
            cutoff = effective_date or self._store.get_latest_stock_daily_bar_date()
            if cutoff is None:
                raise ValueError("no completed stock daily bars are available")
            run = self._store.create_signal_review_run(
                signal_id=signal_id,
                definition_version=DEFINITION_VERSION,
                algorithm_version=ALGORITHM_VERSION,
                cadence="weekly",
                effective_date=cutoff,
                parameters={
                    "lookback_years": 10,
                    "recent_rank": 1,
                    "historical_rank_limit": HISTORICAL_LIMIT,
                    "historical_gate": {
                        "has_rank_one": True, "minimum_score": 0.80,
                        "minimum_confidence": 0.60, "minimum_board_count": 2,
                    },
                    "manual_trigger": True,
                    "membership_semantics": "current-membership-snapshot",
                },
            )
            self._thread = threading.Thread(
                target=self._run_guarded,
                args=(str(run["run_id"]), cutoff),
                name="stock-harness-signal-review", daemon=True,
            )
            self._thread.start()
            return run

    def run_sync(self, signal_id: str, effective_date: date) -> dict[str, object]:
        if signal_id != WEEKLY_RECOGNITION_SIGNAL:
            raise ValueError(f"unknown signal: {signal_id}")
        run = self._store.create_signal_review_run(
            signal_id=signal_id, definition_version=DEFINITION_VERSION,
            algorithm_version=ALGORITHM_VERSION, cadence="weekly",
            effective_date=effective_date,
            parameters={"lookback_years": 10, "manual_trigger": True},
        )
        self._execute(str(run["run_id"]), effective_date)
        return self._store.get_signal_review_run(str(run["run_id"]))  # type: ignore[return-value]

    def _run_guarded(self, run_id: str, cutoff: date) -> None:
        try:
            self._execute(run_id, cutoff)
        except Exception as error:
            self._store.fail_signal_review_run(run_id, str(error))
            LOGGER.exception("signal_review_run_failed run_id=%s", run_id)

    def _execute(self, run_id: str, cutoff: date) -> None:
        started = time.perf_counter()
        boards = self._boards()
        memberships: dict[str, list[dict[str, object]]] = {}
        stocks: dict[str, dict[str, object]] = {}
        total = len(boards)
        self._progress(run_id, "memberships", total, 0)
        for index, board in enumerate(boards, 1):
            members = [
                item for item in self._store.list_board_members(str(board["symbol"]), 5000)
                if item.get("available") is not False and item.get("kind") == "stock"
                and not is_risk_name(str(item.get("name") or ""))
            ]
            memberships[str(board["symbol"])] = members
            stocks.update((str(item["symbol"]), item) for item in members)
            if index % 50 == 0 or index == len(boards):
                self._progress(run_id, "memberships", total, index)

        start_date = cutoff - timedelta(days=10 * 366 + 45)
        total = len(stocks)
        features = {}
        self._progress(run_id, "stock-features", total, 0)
        for index, symbol in enumerate(sorted(stocks), 1):
            bars = [_bar_payload(item) for item in self._store.get_daily_bars(
                symbol, start_date, cutoff
            )]
            feature = calculate_stock_features(symbol, bars)
            if feature is not None:
                features[symbol] = feature
            if index % 50 == 0 or index == total:
                self._progress(run_id, "stock-features", total, index)

        assignments: list[dict[str, object]] = []
        total = len(boards)
        self._progress(run_id, "board-ranking", total, 0)
        ranked_boards = 0
        for index, board in enumerate(boards, 1):
            symbol = str(board["symbol"])
            member_features = [
                features[str(item["symbol"])] for item in memberships[symbol]
                if str(item["symbol"]) in features
            ]
            board_bars = [_bar_payload(item) for item in self._store.get_daily_bars(
                symbol, start_date, cutoff
            )]
            board_returns = compact_returns(board_bars)
            rankings = {
                RECENT_PROFILE: rank_board_leaders(
                    member_features, board_returns, RECENT_PROFILE, 2
                ),
                HISTORICAL_PROFILE: rank_board_leaders(
                    member_features, board_returns, HISTORICAL_PROFILE, HISTORICAL_LIMIT
                ),
            }
            if all(len(value) >= 2 for value in rankings.values()):
                ranked_boards += 1
                names = {str(item["symbol"]): str(item["name"]) for item in memberships[symbol]}
                for profile, ranked in rankings.items():
                    for item in ranked:
                        assignments.append({
                            "profile": profile, "board_symbol": symbol,
                            "board_name": str(board["name"]),
                            "board_classification": _classification(board),
                            "member_symbol": item.symbol,
                            "member_name": names.get(item.symbol, item.symbol),
                            "rank": item.rank, "score": item.score,
                            "confidence": item.confidence, "components": item.components,
                        })
            if index % 50 == 0 or index == total:
                self._progress(run_id, "board-ranking", total, index)

        current = _aggregate_assignments(assignments)
        previous = {
            str(item["item_key"]): item
            for item in self._store.get_prior_signal_review_items(run_id)
            if bool(item["active"])
        }
        items = _compare_items(current, previous)
        digest = hashlib.sha256(json.dumps(
            [{"key": item["item_key"], "score": item["score"]}
             for item in items if item["active"]],
            ensure_ascii=False, sort_keys=True,
        ).encode("utf-8")).hexdigest()
        summary = {
            "board_count": len(boards), "stock_count": len(stocks),
            "ranked_board_count": ranked_boards,
            "assignment_count": len(assignments),
            "membership_semantics": "current membership snapshot; not point-in-time history",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
        self._store.complete_signal_review_run(
            run_id, items=items, summary=summary, input_digest=digest,
        )
        LOGGER.info(
            "signal_review_run_completed run_id=%s effective_date=%s boards=%s stocks=%s items=%s elapsed_ms=%.1f",
            run_id, cutoff, len(boards), len(stocks),
            sum(bool(item["active"]) for item in items),
            (time.perf_counter() - started) * 1000,
        )

    def _boards(self) -> list[dict[str, object]]:
        result: dict[str, dict[str, object]] = {}
        for classification in ("concept", "industry"):
            offset = 0
            while True:
                page = self._store.search_instruments(
                    classification=classification, active=True, limit=500, offset=offset,
                )
                result.update((
                    str(item["symbol"]), {**item, "signal_classification": classification}
                ) for item in page)
                if len(page) < 500:
                    break
                offset += len(page)
        return [result[key] for key in sorted(result)]

    def _progress(self, run_id: str, phase: str, total: int, done: int) -> None:
        self._store.update_signal_review_progress(
            run_id, phase=phase, work_total=total, work_done=done,
        )
        if done == total or (done and done % 500 == 0):
            LOGGER.info("signal_review_progress run_id=%s phase=%s done=%s total=%s",
                        run_id, phase, done, total)


def _bar_payload(bar) -> dict[str, object]:
    return {
        "trade_date": bar.trade_date.isoformat(), "open": bar.open,
        "high": bar.high, "low": bar.low, "close": bar.close, "volume": bar.volume,
    }


def _classification(board: dict[str, object]) -> str:
    return str(board.get("signal_classification") or "concept")


def _aggregate_assignments(assignments: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for item in assignments:
        profile = str(item["profile"])
        if profile == RECENT_PROFILE and int(item["rank"]) != 1:
            continue
        grouped[(profile, str(item["member_symbol"]))].append(item)
    selected: list[dict[str, object]] = []
    for (profile, symbol), rows in grouped.items():
        board_names = {str(item["board_name"]) for item in rows}
        max_score = max(float(item["score"]) for item in rows)
        max_confidence = max(float(item["confidence"]) for item in rows)
        has_rank_one = any(int(item["rank"]) == 1 for item in rows)
        if profile == HISTORICAL_PROFILE and not (
            has_rank_one and max_score >= 0.80 and max_confidence >= 0.60
            and len(board_names) >= 2
        ):
            continue
        ordered = sorted(rows, key=lambda item: (
            int(item["rank"]), -float(item["score"]), str(item["board_name"])
        ))
        evidence = [{
            "evidence_id": str(uuid4()), "alias": f"S{index}",
            "evidence_type": "board-recognition-ranking",
            "payload": {
                "board_symbol": row["board_symbol"], "board_name": row["board_name"],
                "board_classification": row["board_classification"],
                "rank": row["rank"], "score": row["score"],
                "confidence": row["confidence"], "components": row["components"],
            },
        } for index, row in enumerate(ordered[:12], 1)]
        selected.append({
            "item_id": str(uuid4()), "item_key": f"{profile}:{symbol}",
            "symbol": symbol, "profile": profile, "score": max_score,
            "confidence": max_confidence,
            "payload": {
                "board_count": len(board_names),
                "rank_one_count": sum(int(item["rank"]) == 1 for item in rows),
                "board_names": sorted(board_names),
            },
            "evidence": evidence,
        })
    selected.sort(key=lambda item: (
        0 if item["profile"] == RECENT_PROFILE else 1,
        -float(item["score"]), -float(item["confidence"]), str(item["symbol"]),
    ))
    counters = defaultdict(int)
    for item in selected:
        counters[str(item["profile"])] += 1
        item["rank"] = counters[str(item["profile"])]
    return selected


def _compare_items(
    current: list[dict[str, object]], previous: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    current_keys = {str(item["item_key"]) for item in current}
    result = [{
        **item, "active": True,
        "change_type": "retained" if item["item_key"] in previous else "added",
    } for item in current]
    removed_by_profile: dict[str, int] = defaultdict(int)
    for key, item in previous.items():
        if key in current_keys:
            continue
        profile = str(item["profile"])
        removed_by_profile[profile] += 1
        result.append({
            "item_id": str(uuid4()), "item_key": key,
            "rank": removed_by_profile[profile], "symbol": item["symbol"],
            "profile": profile, "change_type": "removed", "active": False,
            "score": item["score"], "confidence": item["confidence"],
            "payload": item["payload"], "evidence": item["evidence"],
        })
    return result
