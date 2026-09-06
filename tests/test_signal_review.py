from datetime import date

from fastapi.testclient import TestClient

from stock_harness.api import create_app
from stock_harness.models import Instrument, InstrumentKind
from stock_harness.signal_review import WEEKLY_RECOGNITION_SIGNAL, _aggregate_assignments
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


def test_signal_definition_api_is_manual_and_versioned() -> None:
    store = SQLiteMarketDataStore(":memory:")
    with TestClient(create_app(store=store)) as client:
        response = client.get("/api/signals/definitions")
        assert response.status_code == 200
        definition = response.json()["items"][0]
        assert definition["signal_id"] == WEEKLY_RECOGNITION_SIGNAL
        assert definition["manual_only"] is True
        assert definition["cadence"] == "weekly"
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
    assert persisted["evidence"][0]["code"] == "S1"
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


def _item(symbol: str, change_type: str, rank: int = 1) -> dict[str, object]:
    return {
        "item_id": f"item-{symbol}", "item_key": f"recent:{symbol}",
        "rank": rank, "symbol": symbol, "profile": "recent",
        "change_type": change_type, "active": change_type != "removed",
        "score": 0.91, "confidence": 0.72,
        "payload": {"board_count": 2},
        "evidence": [{
            "evidence_id": f"evidence-{symbol}", "alias": "S1",
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
