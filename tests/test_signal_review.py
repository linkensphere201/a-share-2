from datetime import date

from fastapi.testclient import TestClient

from stock_harness.api import create_app
from stock_harness.models import Instrument, InstrumentKind
from stock_harness.signal_review import (
    DAILY_MARKET_BOARD_SIGNAL, WEEKLY_RECOGNITION_SIGNAL,
    _aggregate_assignments, _assign_evidence_aliases,
    _compare_items, _result_digest,
)
from stock_harness.daily_signal_analysis import analyze_daily_series, render_board_summary
from stock_harness.models import DailyBar, StockDailyLimit
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.chat_context import build_signal_chat_context


def test_signal_review_snapshots_preserve_revisions_and_diffs() -> None:
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("000001.SZ", "Alpha", InstrumentKind.STOCK, "SZ"),
        Instrument("000002.SZ", "Beta", InstrumentKind.STOCK, "SZ"),
    ])
    first = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL,
        definition_version="definition-v1", algorithm_version="algorithm-v1",
        cadence="weekly", effective_date=date(2026, 9, 4), parameters={},
    )
    store.complete_signal_review_run(
        str(first["run_id"]), items=[_item("000001.SZ", "added")],
        summary={"note": "first"}, input_digest="digest-1",
    )
    second = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL,
        definition_version="definition-v1", algorithm_version="algorithm-v1",
        cadence="weekly", effective_date=date(2026, 9, 4), parameters={},
    )
    assert second["revision"] == 2
    assert second["prior_run_id"] == first["run_id"]
    store.complete_signal_review_run(
        str(second["run_id"]),
        items=[_item("000001.SZ", "retained"), _item("000002.SZ", "added", rank=2)],
        summary={"note": "second"}, input_digest="digest-2",
    )

    stored = store.get_signal_review_run(str(second["run_id"]))
    assert stored is not None
    assert stored["item_count"] == 2
    assert stored["added_count"] == 1
    assert stored["retained_count"] == 1
    items = store.list_signal_review_items(str(second["run_id"]))
    assert [item["symbol"] for item in items] == ["000001.SZ", "000002.SZ"]
    assert items[0]["evidence"][0]["alias"] == "S1"
    store.close()


def test_signal_review_only_compares_compatible_runs() -> None:
    store = SQLiteMarketDataStore(":memory:")
    compatible = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL,
        definition_version="definition-v1", algorithm_version="algorithm-v1",
        cadence="weekly", effective_date=date(2026, 9, 1), parameters={"window": 10},
    )
    store.complete_signal_review_run(
        str(compatible["run_id"]), items=[], summary={}, input_digest="first",
    )
    incompatible = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL,
        definition_version="definition-v1", algorithm_version="algorithm-v2",
        cadence="weekly", effective_date=date(2026, 9, 2), parameters={"window": 10},
    )
    assert incompatible["prior_run_id"] is None
    store.fail_signal_review_run(str(incompatible["run_id"]), "test")
    changed_parameters = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL,
        definition_version="definition-v1", algorithm_version="algorithm-v1",
        cadence="weekly", effective_date=date(2026, 9, 3), parameters={"window": 20},
    )
    assert changed_parameters["prior_run_id"] is None
    store.fail_signal_review_run(str(changed_parameters["run_id"]), "test")
    next_compatible = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL,
        definition_version="definition-v1", algorithm_version="algorithm-v1",
        cadence="weekly", effective_date=date(2026, 9, 4), parameters={"window": 10},
    )
    assert next_compatible["prior_run_id"] == compatible["run_id"]
    store.close()


def test_removed_signal_evidence_is_realiased_after_current_items() -> None:
    current = [_item("000001.SZ", "added")]
    previous_item = _item("000002.SZ", "added")
    result = _compare_items(current, {str(previous_item["item_key"]): previous_item})
    _assign_evidence_aliases(result)
    assert [evidence["alias"] for item in result for evidence in item["evidence"]] == [
        "S1", "S2",
    ]


def test_signal_result_digest_covers_payload_and_evidence() -> None:
    first = [_item("000001.SZ", "added")]
    second = [_item("000001.SZ", "added")]
    second[0]["evidence"][0]["payload"] = {"board_name": "Changed"}
    assert _result_digest(first) != _result_digest(second)


def test_signal_review_rejects_duplicate_evidence_aliases() -> None:
    import pytest

    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("000001.SZ", "Alpha", InstrumentKind.STOCK, "SZ"),
        Instrument("000002.SZ", "Beta", InstrumentKind.STOCK, "SZ"),
    ])
    run = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL,
        definition_version="definition-v1", algorithm_version="algorithm-v1",
        cadence="weekly", effective_date=date(2026, 9, 4), parameters={},
    )
    with pytest.raises(ValueError, match="aliases"):
        duplicate = _item("000002.SZ", "added", 2)
        duplicate["evidence"][0]["alias"] = "S1"
        store.complete_signal_review_run(
            str(run["run_id"]),
            items=[_item("000001.SZ", "added"), duplicate],
            summary={}, input_digest="duplicate",
        )
    store.close()


def test_signal_definition_api_is_manual_and_versioned() -> None:
    store = SQLiteMarketDataStore(":memory:")
    with TestClient(create_app(store=store)) as client:
        response = client.get("/api/signals/definitions")
        assert response.status_code == 200
        definition = response.json()["items"][0]
        assert definition["signal_id"] == WEEKLY_RECOGNITION_SIGNAL
        assert definition["manual_only"] is True
        assert definition["cadence"] == "weekly"
        daily = next(item for item in response.json()["items"]
                     if item["signal_id"] == DAILY_MARKET_BOARD_SIGNAL)
        assert daily["manual_only"] is True
        assert daily["cadence"] == "daily"
    store.close()


def test_signal_chat_is_run_bound_and_snapshots_only_selected_items() -> None:
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("000001.SZ", "Alpha", InstrumentKind.STOCK, "SZ"),
        Instrument("000002.SZ", "Beta", InstrumentKind.STOCK, "SZ"),
    ])
    run = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL,
        definition_version="definition-v1", algorithm_version="algorithm-v1",
        cadence="weekly", effective_date=date(2026, 9, 4), parameters={"top": 2},
    )
    store.complete_signal_review_run(
        str(run["run_id"]),
        items=[_item("000001.SZ", "added"), _item("000002.SZ", "retained", 2)],
        summary={"note": "frozen"}, input_digest="signal-digest",
    )
    conversation = store.get_or_create_signal_chat_conversation(run_id=str(run["run_id"]))
    context = build_signal_chat_context(
        store, run_id=str(run["run_id"]), selected_item_ids=["item-000002.SZ"],
    )
    turn = store.create_chat_turn(
        conversation_id=str(conversation["conversation_id"]), content="复核本期新增",
        template_id="signal-entry-exit", template_version="v1", context=context,
    )

    assert conversation["context_kind"] == "signal_run"
    assert conversation["context_id"] == run["run_id"]
    assert [item["symbol"] for item in context["selected_items"]] == ["000002.SZ"]
    assert context["signal"]["summary"] == {"note": "frozen"}
    persisted = store.get_chat_turn_context(str(turn["turn_id"]))
    assert persisted is not None
    assert persisted["context_kind"] == "signal_run"
    assert persisted["evidence"][0]["code"] == "S2"
    assert store.get_signal_review_run(str(run["run_id"]))["summary"] == {"note": "frozen"}
    store.close()


def test_signal_chat_rejects_foreign_or_excessive_selected_items() -> None:
    store = SQLiteMarketDataStore(":memory:")
    run = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL,
        definition_version="definition-v1", algorithm_version="algorithm-v1",
        cadence="weekly", effective_date=date(2026, 9, 4), parameters={},
    )
    store.complete_signal_review_run(
        str(run["run_id"]), items=[], summary={}, input_digest="empty",
    )

    import pytest
    with pytest.raises(ValueError, match="do not belong"):
        build_signal_chat_context(
            store, run_id=str(run["run_id"]), selected_item_ids=["foreign-item"],
        )
    with pytest.raises(ValueError, match="at most 20"):
        build_signal_chat_context(
            store, run_id=str(run["run_id"]),
            selected_item_ids=[f"item-{index}" for index in range(21)],
        )
    store.close()


def test_weekly_recognition_keeps_recent_rank_one_and_strict_historical_high_weight() -> None:
    assignments = [
        _assignment("recent", "000001.SZ", "CPO", 1, .75, .55),
        _assignment("recent", "000002.SZ", "CPO", 2, .90, .80),
        _assignment("historical", "000001.SZ", "CPO", 1, .84, .66),
        _assignment("historical", "000001.SZ", "算力", 2, .82, .62),
        _assignment("historical", "000002.SZ", "CPO", 1, .95, .80),
    ]

    result = _aggregate_assignments(assignments)

    assert {item["item_key"] for item in result} == {
        "recent:000001.SZ", "historical:000001.SZ",
    }
    historical = next(item for item in result if item["profile"] == "historical")
    assert historical["payload"]["board_count"] == 2
    aliases = [
        evidence["alias"] for item in result for evidence in item["evidence"]
    ]
    assert aliases == [f"S{index}" for index in range(1, len(aliases) + 1)]


def test_daily_observation_is_causal_and_template_rendered_without_ai() -> None:
    bars = _daily_bars("BK001.DC", 140, falling=True)
    cutoff = bars[129].trade_date
    first = analyze_daily_series("BK001.DC", bars[:130], cutoff)
    changed_future = [*bars[:130], DailyBar(
        "BK001.DC", bars[130].trade_date, 100, 110, 90, 105, 99_000_000,
    )]
    replay = analyze_daily_series("BK001.DC", changed_future, cutoff)

    assert first["input_digest"] == replay["input_digest"]
    assert first["metrics"] == replay["metrics"]
    code, rendered = render_board_summary(first, None)
    assert code
    assert all(label in rendered for label in ("结论", "形态", "价", "量", "近期对比", "确认/失效"))


def test_daily_signal_persists_every_board_but_displays_attention_only() -> None:
    store = SQLiteMarketDataStore(":memory:")
    instruments = [
        Instrument("000001.SH", "上证指数", InstrumentKind.INDEX, "SH"),
        Instrument("SHAMV.A", "活跃市值", InstrumentKind.INDEX, "LOCAL"),
        Instrument("BK001.DC", "平稳板块", InstrumentKind.SECTOR, "DC"),
        Instrument("BK002.DC", "放量板块", InstrumentKind.SECTOR, "DC"),
    ]
    store.upsert_instruments(instruments)
    baseline = _daily_bars("000001.SH", 140)
    active = _daily_bars("SHAMV.A", 140)
    quiet = _daily_bars("BK001.DC", 140)
    loud = _daily_bars("BK002.DC", 140)
    loud[-1] = DailyBar(
        loud[-1].symbol, loud[-1].trade_date, loud[-1].open,
        loud[-1].high, loud[-1].low, loud[-1].close, loud[-1].volume * 3,
    )
    store.upsert_daily_bars("test", [*baseline, *active, *quiet, *loud])

    result = __import__("stock_harness.signal_review", fromlist=["SignalReviewService"])
    service = result.SignalReviewService(store)
    service._boards = lambda: [
        {"symbol": "BK001.DC", "name": "平稳板块"},
        {"symbol": "BK002.DC", "name": "放量板块"},
    ]
    run = service.run_sync(DAILY_MARKET_BOARD_SIGNAL, baseline[-1].trade_date)

    assert run["summary"]["expected_board_count"] == 2
    assert run["summary"]["saved_observation_count"] == 2
    assert store.count_board_daily_observations(str(run["run_id"])) == 2
    observations = store.list_board_daily_observations(run_id=str(run["run_id"]), limit=10)
    assert {item["symbol"] for item in observations} == {"BK001.DC", "BK002.DC"}
    items = store.list_signal_review_items(str(run["run_id"]))
    assert {item["symbol"] for item in items if item["profile"] == "attention"} == {"BK002.DC"}
    assert run["summary"]["ai_used"] is False
    store.close()


def test_manual_attention_keeps_neutral_board_in_compact_result() -> None:
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("000001.SH", "上证指数", InstrumentKind.INDEX, "SH"),
        Instrument("SHAMV.A", "活跃市值", InstrumentKind.INDEX, "LOCAL"),
        Instrument("BK001.DC", "平稳板块", InstrumentKind.SECTOR, "DC"),
    ])
    bars = _daily_bars("000001.SH", 140)
    board = _daily_bars("BK001.DC", 140)
    store.upsert_daily_bars("test", [*bars, *board, *_daily_bars("SHAMV.A", 140)])
    store.set_signal_attention(
        DAILY_MARKET_BOARD_SIGNAL, "BK001.DC", manual_pinned=True,
        effective_date=bars[-1].trade_date,
    )
    result = __import__("stock_harness.signal_review", fromlist=["SignalReviewService"])
    service = result.SignalReviewService(store)
    service._boards = lambda: [{"symbol": "BK001.DC", "name": "平稳板块"}]
    run = service.run_sync(
        DAILY_MARKET_BOARD_SIGNAL, bars[-1].trade_date,
    )
    items = store.list_signal_review_items(str(run["run_id"]))
    assert any(item["symbol"] == "BK001.DC" and item["active"] for item in items)
    assert store.get_signal_attention(
        DAILY_MARKET_BOARD_SIGNAL, "BK001.DC",
    )["status"] == "manual-pinned"
    store.close()


def test_market_emotion_uses_official_limits_and_persists_with_run() -> None:
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("000001.SZ", "涨停股", InstrumentKind.STOCK, "SZ"),
        Instrument("000002.SZ", "炸板股", InstrumentKind.STOCK, "SZ"),
        Instrument("000003.SZ", "跌停股", InstrumentKind.STOCK, "SZ"),
    ])
    first = date(2026, 9, 3)
    current = date(2026, 9, 4)
    store.upsert_daily_bars("test", [
        DailyBar("000001.SZ", first, 10, 10, 10, 10, 100),
        DailyBar("000002.SZ", first, 10, 10, 10, 10, 100),
        DailyBar("000003.SZ", first, 10, 10, 10, 10, 100),
        DailyBar("000001.SZ", current, 10.5, 11, 10.5, 11, 300),
        DailyBar("000002.SZ", current, 10.5, 11, 10.2, 10.6, 400),
        DailyBar("000003.SZ", current, 9.5, 9.5, 9, 9, 500),
    ])
    store.upsert_stock_daily_limits("test", [
        StockDailyLimit("000001.SZ", current, 11, 9),
        StockDailyLimit("000002.SZ", current, 11, 9),
        StockDailyLimit("000003.SZ", current, 11, 9),
    ])
    run = store.create_signal_review_run(
        signal_id=DAILY_MARKET_BOARD_SIGNAL,
        definition_version="daily-v1", algorithm_version="algorithm-v1",
        cadence="daily", effective_date=current, parameters={},
    )
    snapshot = store.calculate_market_emotion_snapshot(str(run["run_id"]), current)

    assert snapshot["status"] == "complete"
    assert snapshot["metrics"] == {
        "advance_count": 2, "decline_count": 1, "unchanged_count": 0,
        "breadth": 0.333333, "limit_up_count": 1, "limit_down_count": 1,
        "broken_up_count": 1, "sealing_rate": 0.5, "limit_balance": 0.0,
    }
    assert store.get_market_emotion_snapshot(str(run["run_id"]))["input_digest"] == snapshot["input_digest"]
    store.fail_signal_review_run(str(run["run_id"]), "test cleanup")
    store.close()


def test_signal_observation_and_attention_api() -> None:
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("BK001.DC", "测试板块", InstrumentKind.SECTOR, "DC"),
        Instrument("000001.SZ", "日期基准", InstrumentKind.STOCK, "SZ"),
    ])
    effective = date(2026, 9, 4)
    store.upsert_daily_bars("test", [DailyBar(
        "000001.SZ", effective, 10, 10, 10, 10, 100,
    )])
    run = store.create_signal_review_run(
        signal_id=DAILY_MARKET_BOARD_SIGNAL,
        definition_version="daily-v1", algorithm_version="algorithm-v1",
        cadence="daily", effective_date=effective, parameters={},
    )
    observation = analyze_daily_series("BK001.DC", [], effective)
    store.save_board_daily_observations(str(run["run_id"]), [observation])
    store.fail_signal_review_run(str(run["run_id"]), "fixture complete")

    with TestClient(create_app(store=store)) as client:
        history = client.get(
            f"/api/signals/runs/{run['run_id']}/board-observations",
            params={"symbol": "BK001.DC"},
        )
        assert history.status_code == 200
        assert history.json()["items"][0]["coverage_state"] == "insufficient"
        pinned = client.put(
            f"/api/signals/{DAILY_MARKET_BOARD_SIGNAL}/attention/BK001.DC",
            json={"manual_pinned": True, "effective_date": effective.isoformat()},
        )
        assert pinned.status_code == 200
        attention = client.get(
            f"/api/signals/{DAILY_MARKET_BOARD_SIGNAL}/attention"
        ).json()["items"]
        assert attention[0]["status"] == "manual-pinned"
    store.close()


def _item(symbol: str, change_type: str, rank: int = 1) -> dict[str, object]:
    return {
        "item_id": f"item-{symbol}", "item_key": f"recent:{symbol}",
        "rank": rank, "symbol": symbol, "profile": "recent",
        "change_type": change_type, "active": change_type != "removed",
        "score": 0.91, "confidence": 0.72,
        "payload": {"board_count": 2},
        "evidence": [{
            "evidence_id": f"evidence-{symbol}", "alias": f"S{rank}",
            "evidence_type": "board-recognition-ranking",
            "payload": {"board_name": "CPO"},
        }],
    }


def _assignment(
    profile: str, symbol: str, board: str, rank: int, score: float, confidence: float,
) -> dict[str, object]:
    return {
        "profile": profile, "board_symbol": f"BOARD-{board}", "board_name": board,
        "board_classification": "concept", "member_symbol": symbol,
        "member_name": symbol, "rank": rank, "score": score,
        "confidence": confidence, "components": {"association": .8},
    }


def _daily_bars(symbol: str, count: int, falling: bool = False) -> list[DailyBar]:
    from datetime import timedelta

    start = date(2026, 1, 1)
    result = []
    for index in range(count):
        close = 100 - index * .12 if falling else 100 + index * .03
        result.append(DailyBar(
            symbol, start + timedelta(days=index), close - .2, close + .5,
            close - .5, close, 1_000_000 + index * 100,
        ))
    return result
