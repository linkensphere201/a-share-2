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
from stock_harness.daily_signal_analysis import (
    ALGORITHM_VERSION as DAILY_ALGORITHM_VERSION,
    CONFIG_VERSION as DAILY_CONFIG_VERSION,
    LOOKBACK_BARS as DAILY_LOOKBACK_BARS,
    analyze_daily_series,
    render_board_summary,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore


LOGGER = logging.getLogger(__name__)
WEEKLY_RECOGNITION_SIGNAL = "weekly-board-recognition"
DEFINITION_VERSION = "weekly-board-recognition-v1"
DAILY_MARKET_BOARD_SIGNAL = "daily-market-board-review"
DAILY_DEFINITION_VERSION = "daily-market-board-review-v1"
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
        }, {
            "signal_id": DAILY_MARKET_BOARD_SIGNAL,
            "name": "每日大盘与板块复盘",
            "description": "保存全板块一级固定分析，并筛选值得持续关注的异动。",
            "cadence": "daily",
            "definition_version": DAILY_DEFINITION_VERSION,
            "algorithm_version": DAILY_ALGORITHM_VERSION,
            "manual_only": True,
            "profiles": ["market", "attention"],
        }]

    def start_run(
        self, signal_id: str, effective_date: date | None = None,
    ) -> dict[str, object]:
        if signal_id not in {WEEKLY_RECOGNITION_SIGNAL, DAILY_MARKET_BOARD_SIGNAL}:
            raise ValueError(f"unknown signal: {signal_id}")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise SignalReviewBusyError("another signal review run is already active")
            cutoff = effective_date or self._store.get_latest_stock_daily_bar_date()
            if cutoff is None:
                raise ValueError("no completed stock daily bars are available")
            daily = signal_id == DAILY_MARKET_BOARD_SIGNAL
            run = self._store.create_signal_review_run(
                signal_id=signal_id,
                definition_version=(DAILY_DEFINITION_VERSION if daily else DEFINITION_VERSION),
                algorithm_version=(DAILY_ALGORITHM_VERSION if daily else ALGORITHM_VERSION),
                cadence="daily" if daily else "weekly",
                effective_date=cutoff,
                parameters=_daily_run_parameters() if daily else _run_parameters(),
            )
            self._thread = threading.Thread(
                target=self._run_guarded,
                args=(str(run["run_id"]), cutoff, signal_id),
                name="stock-harness-signal-review", daemon=True,
            )
            self._thread.start()
            return run

    def run_sync(self, signal_id: str, effective_date: date) -> dict[str, object]:
        if signal_id not in {WEEKLY_RECOGNITION_SIGNAL, DAILY_MARKET_BOARD_SIGNAL}:
            raise ValueError(f"unknown signal: {signal_id}")
        daily = signal_id == DAILY_MARKET_BOARD_SIGNAL
        run = self._store.create_signal_review_run(
            signal_id=signal_id,
            definition_version=DAILY_DEFINITION_VERSION if daily else DEFINITION_VERSION,
            algorithm_version=DAILY_ALGORITHM_VERSION if daily else ALGORITHM_VERSION,
            cadence="daily" if daily else "weekly",
            effective_date=effective_date,
            parameters=_daily_run_parameters() if daily else _run_parameters(),
        )
        if daily:
            self._execute_daily(str(run["run_id"]), effective_date)
        else:
            self._execute(str(run["run_id"]), effective_date)
        return self._store.get_signal_review_run(str(run["run_id"]))  # type: ignore[return-value]

    def _run_guarded(self, run_id: str, cutoff: date, signal_id: str) -> None:
        try:
            if signal_id == DAILY_MARKET_BOARD_SIGNAL:
                self._execute_daily(run_id, cutoff)
            else:
                self._execute(run_id, cutoff)
        except Exception as error:
            self._store.fail_signal_review_run(run_id, str(error))
            LOGGER.exception("signal_review_run_failed run_id=%s", run_id)

    def _execute_daily(self, run_id: str, cutoff: date) -> None:
        started = time.perf_counter()
        boards = self._boards()
        benchmark = self._store.get_recent_daily_bars(
            "000001.SH", cutoff, DAILY_LOOKBACK_BARS,
        )
        run = self._store.get_signal_review_run(run_id)
        prior_run_id = str(run["prior_run_id"]) if run and run.get("prior_run_id") else None
        prior_observations = {
            str(item["symbol"]): item
            for item in self._store.list_board_daily_observations(
                run_id=prior_run_id, limit=5000,
            )
        } if prior_run_id else {}
        observations: list[dict[str, object]] = []
        self._progress(run_id, "board-observations", len(boards), 0)
        for offset in range(0, len(boards), 100):
            page = boards[offset:offset + 100]
            series = self._store.get_recent_daily_bars_many(
                [str(board["symbol"]) for board in page], cutoff, DAILY_LOOKBACK_BARS,
            )
            batch = [
                analyze_daily_series(
                    str(board["symbol"]), series.get(str(board["symbol"]), []), cutoff,
                    benchmark_bars=benchmark,
                )
                for board in page
            ]
            self._store.save_board_daily_observations(run_id, batch)
            observations.extend(batch)
            done = min(offset + len(page), len(boards))
            self._progress(run_id, "board-observations", len(boards), done)

        attention_registry = {
            str(item["symbol"]): item
            for item in self._store.list_signal_attention(DAILY_MARKET_BOARD_SIGNAL)
        }
        promoted = []
        for observation in observations:
            symbol = str(observation["symbol"])
            registry = attention_registry.get(symbol)
            if bool(observation["attention_eligible"]):
                self._store.promote_signal_attention(
                    DAILY_MARKET_BOARD_SIGNAL, symbol, cutoff,
                    [str(item) for item in observation["attention_reasons"]],
                )
            if bool(observation["attention_eligible"]) or (
                registry is not None and str(registry["status"]) != "inactive"
            ):
                promoted.append(observation)

        board_names = {str(board["symbol"]): str(board["name"]) for board in boards}
        previous_items = {
            str(item["item_key"]): item
            for item in self._store.get_prior_signal_review_items(run_id)
            if bool(item["active"])
        }
        emotion = self._store.calculate_market_emotion_snapshot(run_id, cutoff)
        items = self._daily_market_items(cutoff, previous_items, emotion)
        for observation in promoted:
            symbol = str(observation["symbol"])
            item_key = f"attention:{symbol}"
            prior_observation = prior_observations.get(symbol)
            prior_payload = None
            if prior_observation is not None:
                prior_code, _ = render_board_summary(prior_observation, None)
                prior_payload = {
                    "payload": {"conclusion_code": prior_code},
                    "effective_date": prior_observation["effective_date"],
                }
            conclusion_code, rendered = render_board_summary(observation, prior_payload)
            metrics = observation.get("metrics", {})
            reasons = list(observation.get("attention_reasons", []))
            score = _daily_attention_score(observation)
            items.append({
                "item_id": str(uuid4()), "item_key": item_key,
                "rank": 0, "symbol": symbol, "profile": "attention",
                "change_type": "retained" if item_key in previous_items else "added",
                "active": True, "score": score,
                "confidence": _daily_confidence(observation),
                "payload": {
                    "conclusion_code": conclusion_code,
                    "state_codes": observation["state_codes"],
                    "attention_reasons": reasons,
                    "rendered_summary": rendered,
                    "metrics": metrics,
                    "effective_date": cutoff.isoformat(),
                    "observation_input_digest": observation["input_digest"],
                    "deep_analysis_state": observation["deep_analysis_state"],
                    "board_name": board_names.get(symbol, symbol),
                },
                "evidence": [{
                    "evidence_id": str(uuid4()), "alias": "",
                    "evidence_type": "board-daily-observation",
                    "payload": {
                        "symbol": symbol, "effective_date": cutoff.isoformat(),
                        "input_digest": observation["input_digest"],
                        "state_codes": observation["state_codes"],
                    },
                }],
            })
        items.extend(_removed_daily_items(items, previous_items))
        _rank_daily_items(items)
        _assign_evidence_aliases(items)
        complete_count = sum(item["coverage_state"] == "complete" for item in observations)
        missing_count = len(observations) - complete_count
        summary = {
            "expected_board_count": len(boards),
            "saved_observation_count": len(observations),
            "complete_observation_count": complete_count,
            "missing_observation_count": missing_count,
            "promoted_board_count": len(promoted),
            "displayed_item_count": sum(bool(item["active"]) for item in items),
            "attention_registry_count": len(self._store.list_signal_attention(
                DAILY_MARKET_BOARD_SIGNAL,
            )),
            "emotion": emotion,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "ai_used": False,
        }
        digest = _result_digest(items + [{
            "active": True, "item_key": "all-board-observations",
            "rank": 0, "score": 0, "confidence": 1,
            "payload": {"digests": sorted(str(item["input_digest"]) for item in observations)},
            "evidence": [],
        }])
        self._store.complete_signal_review_run(
            run_id, items=items, summary=summary, input_digest=digest,
        )
        LOGGER.info(
            "daily_signal_review_completed run_id=%s date=%s observations=%s promoted=%s elapsed_ms=%.1f",
            run_id, cutoff, len(observations), len(promoted),
            (time.perf_counter() - started) * 1000,
        )

    def _daily_market_items(
        self, cutoff: date, previous: dict[str, dict[str, object]],
        emotion: dict[str, object],
    ) -> list[dict[str, object]]:
        items = []
        for rank, symbol in enumerate(("000001.SH", "SHAMV.A"), 1):
            bars = self._store.get_recent_daily_bars(symbol, cutoff, DAILY_LOOKBACK_BARS)
            observation = analyze_daily_series(
                symbol, bars, cutoff,
                benchmark_bars=bars if symbol == "000001.SH" else self._store.get_recent_daily_bars(
                    "000001.SH", cutoff, DAILY_LOOKBACK_BARS,
                ),
            )
            code, rendered = render_board_summary(observation, previous.get(f"market:{symbol}"))
            if symbol == "000001.SH":
                rendered = rendered.replace(
                    "- 近期对比：", f"- 情绪：{_render_emotion(emotion)}\n- 近期对比：",
                )
            items.append({
                "item_id": str(uuid4()), "item_key": f"market:{symbol}",
                "rank": rank, "symbol": symbol, "profile": "market",
                "change_type": "retained" if f"market:{symbol}" in previous else "added",
                "active": True, "score": _daily_attention_score(observation),
                "confidence": _daily_confidence(observation),
                "payload": {
                    "conclusion_code": code, "state_codes": observation["state_codes"],
                    "rendered_summary": rendered, "metrics": observation["metrics"],
                    "effective_date": cutoff.isoformat(),
                    "emotion": emotion if symbol == "000001.SH" else {
                        "status": "represented-by-market-overview",
                    },
                },
                "evidence": [{
                    "evidence_id": str(uuid4()), "alias": "",
                    "evidence_type": "market-daily-series",
                    "payload": {"symbol": symbol, "effective_date": cutoff.isoformat()},
                }],
            })
        return items

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
        _assign_evidence_aliases(items)
        digest = _result_digest(items)
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


def _run_parameters() -> dict[str, object]:
    return {
        "lookback_years": 10,
        "recent_rank": 1,
        "historical_rank_limit": HISTORICAL_LIMIT,
        "historical_gate": {
            "has_rank_one": True, "minimum_score": 0.80,
            "minimum_confidence": 0.60, "minimum_board_count": 2,
        },
        "manual_trigger": True,
        "membership_semantics": "current-membership-snapshot",
    }


def _daily_run_parameters() -> dict[str, object]:
    return {
        "lookback_bars": DAILY_LOOKBACK_BARS,
        "minimum_bars": 120,
        "attention_filter": DAILY_CONFIG_VERSION,
        "manual_trigger": True,
        "full_observation_persistence": True,
        "deep_analysis_limit": 60,
    }


def _daily_attention_score(observation: dict[str, object]) -> float:
    reasons = {str(item) for item in observation.get("attention_reasons", [])}
    weights = {
        "1y-descending-envelope-broken": .28,
        "6m-descending-envelope-broken": .24,
        "3m-descending-envelope-broken": .20,
        "bullish-boundary-proximity": .20,
        "downside-exhaustion": .20,
        "sudden-volume-expansion": .16,
        "boundary-volume-contraction": .12,
        "relative-strength-regime": .10,
    }
    return min(1.0, round(sum(weights.get(reason, .08) for reason in reasons), 6))


def _daily_confidence(observation: dict[str, object]) -> float:
    if observation.get("coverage_state") != "complete":
        return 0.0
    reasons = len(observation.get("attention_reasons", []))
    disqualifiers = len(observation.get("disqualifiers", []))
    return max(0.0, min(1.0, round(.55 + min(reasons, 3) * .1 - disqualifiers * .2, 6)))


def _render_emotion(emotion: dict[str, object]) -> str:
    metrics = emotion.get("metrics")
    if not isinstance(metrics, dict) or emotion.get("status") == "unavailable":
        return "正式收盘情绪输入不可用，不以零值替代。"
    breadth = metrics.get("breadth")
    breadth_text = "不可用" if breadth is None else f"{float(breadth):+.2f}"
    if metrics.get("limit_up_count") is None:
        limit_text = "涨跌停价格覆盖不足"
    else:
        sealing = metrics.get("sealing_rate")
        sealing_text = "不可用" if sealing is None else f"{float(sealing) * 100:.1f}%"
        limit_text = (
            f"涨停{metrics['limit_up_count']}、跌停{metrics['limit_down_count']}、"
            f"破板{metrics['broken_up_count']}、封板率{sealing_text}"
        )
    return (
        f"上涨{metrics.get('advance_count', 0)}、下跌{metrics.get('decline_count', 0)}，"
        f"宽度{breadth_text}；{limit_text}。"
    )


def _removed_daily_items(
    current: list[dict[str, object]], previous: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    current_keys = {str(item["item_key"]) for item in current}
    removed = []
    for key, item in previous.items():
        if key in current_keys or str(item.get("profile")) == "market":
            continue
        removed.append({
            "item_id": str(uuid4()), "item_key": key, "rank": 0,
            "symbol": item["symbol"], "profile": item["profile"],
            "change_type": "removed", "active": False,
            "score": item["score"], "confidence": item["confidence"],
            "payload": {
                **dict(item.get("payload", {})),
                "transition": "invalidated-or-left-attention",
            },
            "evidence": [{
                **evidence, "evidence_id": str(uuid4()), "alias": "",
            } for evidence in item.get("evidence", [])],
        })
    return removed


def _rank_daily_items(items: list[dict[str, object]]) -> None:
    market_rank = 0
    attention_rank = 0
    for item in sorted(items, key=lambda value: (
        0 if value["profile"] == "market" else 1,
        not bool(value["active"]), -float(value["score"]), str(value["symbol"]),
    )):
        if item["profile"] == "market":
            market_rank += 1
            item["rank"] = market_rank
        else:
            attention_rank += 1
            item["rank"] = attention_rank


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
            "evidence_id": str(uuid4()), "alias": "",
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
    _assign_evidence_aliases(selected)
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


def _assign_evidence_aliases(items: list[dict[str, object]]) -> None:
    sequence = 0
    for item in items:
        for evidence in item.get("evidence", []):
            sequence += 1
            evidence["alias"] = f"S{sequence}"


def _result_digest(items: list[dict[str, object]]) -> str:
    normalized = []
    for item in items:
        if not bool(item["active"]):
            continue
        normalized.append({
            "item_key": item["item_key"], "rank": item["rank"],
            "score": item["score"], "confidence": item["confidence"],
            "payload": item.get("payload", {}),
            "evidence": [{
                "evidence_type": evidence["evidence_type"],
                "source_run_id": evidence.get("source_run_id"),
                "source_item_id": evidence.get("source_item_id"),
                "payload": evidence.get("payload", {}),
            } for evidence in item.get("evidence", [])],
        })
    return hashlib.sha256(json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=lambda value: value.isoformat() if isinstance(value, date) else str(value),
    ).encode("utf-8")).hexdigest()
