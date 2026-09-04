from __future__ import annotations

from datetime import date
from pathlib import Path
import time
from typing import Callable

from fastapi.testclient import TestClient

from stock_harness.analysis_results import (
    AnalysisNamespace, AnalysisRunSpec, GeneratedAnalysisItem, GeneratedItemType,
)
from stock_harness.api import create_app
from stock_harness.models import DailyBar, Instrument, InstrumentKind
from stock_harness.sqlite_store import SQLiteMarketDataStore


class FakeCodexBridge:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.closed = False
        self.interrupts: list[tuple[str, str]] = []

    def status(self) -> dict[str, object]:
        return {
            "available": True, "authenticated": True, "version": "codex-cli 0.146.0",
            "experimental": True, "error": None,
        }

    def start_thread(self, _workdir: Path) -> str:
        return "codex-thread-1"

    def ensure_thread(self, _thread_id: str, _workdir: Path) -> None:
        return None

    def run_turn(
        self,
        _thread_id: str,
        prompt: str,
        on_event: Callable[[str, dict[str, object]], None],
    ) -> tuple[str, str, str]:
        self.prompts.append(prompt)
        on_event("turn", {"turn_id": "codex-turn-1"})
        on_event("delta", {"delta": "关注 [L1]，"})
        on_event("delta", {"delta": "并观察 [K1]。"})
        return "codex-turn-1", "关注 [L1]，并观察 [K1]。", "completed"

    def interrupt(self, thread_id: str, turn_id: str) -> None:
        self.interrupts.append((thread_id, turn_id))

    def close(self) -> None:
        self.closed = True


def _store_with_run() -> tuple[SQLiteMarketDataStore, str]:
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("000001.SZ", "平安银行", InstrumentKind.STOCK, "SZ")
    ])
    store.upsert_daily_bars("test", [
        DailyBar("000001.SZ", date(2026, 8, 30), 10, 11, 9, 10.5, 100),
        DailyBar("000001.SZ", date(2026, 8, 31), 10.5, 12, 10, 11.5, 200),
        DailyBar("000001.SZ", date(2026, 9, 1), 11.5, 13, 11, 12.5, 300),
    ])
    run = store.begin_generated_analysis_run(AnalysisRunSpec(
        system_id="trend", symbol="000001.SZ", timeframe="daily",
        namespace=AnalysisNamespace.OFFICIAL, as_of_date=date(2026, 8, 31),
        input_start_date=date(2026, 8, 1), input_end_date=date(2026, 8, 31),
        input_digest=b"chat-context", algorithm_version="trend-v1",
        config_version="workspace-v1", completion_state="complete",
    ))
    store.complete_generated_analysis_run(run.run_id, [
        GeneratedAnalysisItem("resistance", GeneratedItemType.LINE, {
            "kind": "resistance", "first_pivot_date": "2026-08-01",
            "first_price": 13, "second_pivot_date": "2026-08-20",
            "second_price": 12,
        }),
        GeneratedAnalysisItem("support", GeneratedItemType.ZONE, {
            "kind": "key-level", "lower": 9.9, "upper": 10.1,
        }),
    ], duration_ms=1)
    return store, run.run_id


def test_result_bound_chat_streams_and_persists_a_completed_turn() -> None:
    store, run_id = _store_with_run()
    bridge = FakeCodexBridge()
    with TestClient(create_app(store, codex_bridge=bridge)) as client:
        status = client.get("/api/ai/codex/status")
        conversation = client.post("/api/ai/conversations", json={
            "symbol": "000001.SZ", "timeframe": "daily", "source_run_id": run_id,
        })
        turn = client.post(
            f"/api/ai/conversations/{conversation.json()['conversation_id']}/turns",
            json={"content": "现在是什么形态？", "template_id": "correction"},
        )
        event_response = client.get(f"/api/ai/turns/{turn.json()['turn_id']}/events")
        deadline = time.time() + 2
        persisted = None
        while time.time() < deadline:
            persisted = client.get(
                f"/api/ai/conversations/{conversation.json()['conversation_id']}"
            ).json()
            if persisted["turns"][0]["status"] == "completed":
                break
            time.sleep(0.01)

    assert status.json()["codex"]["authenticated"] is True
    assert conversation.status_code == 201
    assert turn.status_code == 202
    assert "event: delta" in event_response.text
    assert "event: completed" in event_response.text
    assert persisted is not None
    assert persisted["turns"][0]["messages"][1]["content"] == "关注 [L1]，并观察 [K1]。"
    assert "纠错" not in bridge.prompts[0]
    assert "严格审查形态" in bridge.prompts[0]
    assert bridge.closed is True
    store.close()


def test_chat_context_excludes_bars_after_the_bound_analysis_date() -> None:
    store, run_id = _store_with_run()
    conversation = store.get_or_create_chat_conversation(
        symbol="000001.SZ", timeframe="daily", source_run_id=run_id
    )
    from stock_harness.chat_context import build_chat_context
    context = build_chat_context(
        store, symbol="000001.SZ", timeframe="daily", source_run_id=run_id
    )

    assert conversation["source_run_id"] == run_id
    assert [bar["date"] for bar in context["bars"]] == ["2026-08-30", "2026-08-31"]
    assert context["evidence"] == [
        {"code": "L1", "analysis_item_id": "resistance", "kind": "line"},
        {"code": "K1", "analysis_item_id": "support", "kind": "zone"},
    ]
    store.close()


def test_chat_rejects_cross_symbol_run_binding_and_unknown_template() -> None:
    store, run_id = _store_with_run()
    bridge = FakeCodexBridge()
    store.upsert_instruments([
        Instrument("000002.SZ", "万科A", InstrumentKind.STOCK, "SZ")
    ])
    with TestClient(create_app(store, codex_bridge=bridge)) as client:
        wrong = client.post("/api/ai/conversations", json={
            "symbol": "000002.SZ", "timeframe": "daily", "source_run_id": run_id,
        })
        conversation = client.post("/api/ai/conversations", json={
            "symbol": "000001.SZ", "timeframe": "daily", "source_run_id": run_id,
        }).json()
        template = client.post(
            f"/api/ai/conversations/{conversation['conversation_id']}/turns",
            json={"content": "分析", "template_id": "not-a-template"},
        )

    assert wrong.status_code == 422
    assert template.status_code == 422
    store.close()


def test_startup_marks_abandoned_chat_turn_as_failed() -> None:
    store, run_id = _store_with_run()
    conversation = store.get_or_create_chat_conversation(
        symbol="000001.SZ", timeframe="daily", source_run_id=run_id
    )
    from stock_harness.chat_context import build_chat_context
    context = build_chat_context(
        store, symbol="000001.SZ", timeframe="daily", source_run_id=run_id
    )
    store.create_chat_turn(
        conversation_id=str(conversation["conversation_id"]),
        content="unfinished", template_id=None, context=context,
    )

    with TestClient(create_app(store, codex_bridge=FakeCodexBridge())) as client:
        recovered = client.get(
            f"/api/ai/conversations/{conversation['conversation_id']}"
        ).json()

    assert recovered["turns"][0]["status"] == "failed"
    assert recovered["turns"][0]["error"] == "应用退出时对话尚未完成"
    store.close()
