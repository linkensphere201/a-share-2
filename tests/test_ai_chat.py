from __future__ import annotations

from datetime import date
from pathlib import Path
import json
import sqlite3
import threading
import sys
import time
from typing import Callable

from fastapi.testclient import TestClient
import pytest

from stock_harness.analysis_results import (
    AnalysisNamespace, AnalysisRunSpec, GeneratedAnalysisItem, GeneratedItemType,
)
from stock_harness.api import create_app
from stock_harness.models import DailyBar, Instrument, InstrumentKind
from stock_harness.signal_review import WEEKLY_RECOGNITION_SIGNAL
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.codex_app_server import CodexAppServerClient, CodexUnavailableError


class FakeCodexBridge:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.closed = False
        self.interrupts: list[tuple[str, str]] = []
        self.access_profiles: list[str] = []

    def status(self) -> dict[str, object]:
        return {
            "available": True, "authenticated": True, "version": "codex-cli 0.146.0",
            "experimental": True, "error": None,
        }

    def start_thread(
        self, _workdir: Path, access_profile: str = "trend_analysis",
    ) -> str:
        self.access_profiles.append(access_profile)
        return "codex-thread-1"

    def ensure_thread(
        self, _thread_id: str, _workdir: Path,
        access_profile: str = "trend_analysis",
    ) -> None:
        self.access_profiles.append(access_profile)
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
        context_response = client.get(f"/api/ai/turns/{turn.json()['turn_id']}/context")
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
    assert context_response.json()["workspace_reference"].endswith(run_id)
    assert context_response.json()["sources"] == ["test"]
    assert persisted is not None
    assert persisted["turns"][0]["messages"][1]["content"] == "关注 [L1]，并观察 [K1]。"
    assert "纠错" not in bridge.prompts[0]
    assert "严格审查形态" in bridge.prompts[0]
    assert bridge.closed is True
    store.close()


def test_signal_result_chat_uses_signal_template_and_retries_from_saved_context() -> None:
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("000001.SZ", "平安银行", InstrumentKind.STOCK, "SZ")
    ])
    run = store.create_signal_review_run(
        signal_id=WEEKLY_RECOGNITION_SIGNAL,
        definition_version="signal-v1", algorithm_version="recognition-v1",
        cadence="weekly", effective_date=date(2026, 9, 4), parameters={},
    )
    store.complete_signal_review_run(str(run["run_id"]), items=[{
        "item_id": "signal-item-1", "item_key": "recent:000001.SZ", "rank": 1,
        "symbol": "000001.SZ", "profile": "recent", "change_type": "added",
        "active": True, "score": .9, "confidence": .8, "payload": {},
        "evidence": [{
            "evidence_id": "signal-evidence-1", "alias": "S1",
            "evidence_type": "board-recognition-ranking",
            "payload": {"board_name": "银行"},
        }],
    }], summary={"added": 1}, input_digest="signal-digest")
    bridge = FakeCodexBridge()
    with TestClient(create_app(store, codex_bridge=bridge)) as client:
        conversation = client.post("/api/ai/conversations", json={
            "context_kind": "signal_run", "context_id": run["run_id"],
        }).json()
        turn = client.post(
            f"/api/ai/conversations/{conversation['conversation_id']}/turns",
            json={"content": "为什么进入？", "template_id": "signal-entry-exit",
                  "selected_signal_item_ids": ["signal-item-1"]},
        ).json()
        client.get(f"/api/ai/turns/{turn['turn_id']}/events")
        retried = client.post(f"/api/ai/turns/{turn['turn_id']}/retry").json()
        client.get(f"/api/ai/turns/{retried['turn_id']}/events")
        restored = client.get(
            f"/api/ai/conversations/{conversation['conversation_id']}"
        ).json()

    assert conversation["context_kind"] == "signal_run"
    assert len(restored["turns"]) == 2
    assert all(turn["status"] == "completed" for turn in restored["turns"])
    assert len(bridge.prompts) == 2
    assert "不可变信号复盘结果" in bridge.prompts[0]
    assert "进入或退出结果" in bridge.prompts[0]
    assert '"code":"S1"' in bridge.prompts[0]
    assert bridge.access_profiles == ["signal_run", "signal_run"]
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


def test_typed_chat_context_migration_preserves_legacy_conversation(tmp_path: Path) -> None:
    database = tmp_path / "legacy-chat.sqlite3"
    store = SQLiteMarketDataStore(database)
    store.upsert_instruments([
        Instrument("000001.SZ", "平安银行", InstrumentKind.STOCK, "SZ")
    ])
    run = store.begin_generated_analysis_run(AnalysisRunSpec(
        system_id="trend", symbol="000001.SZ", timeframe="daily",
        namespace=AnalysisNamespace.OFFICIAL, as_of_date=date(2026, 8, 31),
        input_start_date=date(2026, 8, 1), input_end_date=date(2026, 8, 31),
        input_digest=b"legacy", algorithm_version="trend-v1",
        config_version="workspace-v1", completion_state="complete",
    ))
    store.complete_generated_analysis_run(run.run_id, [], duration_ms=1)
    conversation = store.get_or_create_chat_conversation(
        symbol="000001.SZ", timeframe="daily", source_run_id=run.run_id,
    )
    store.set_chat_codex_thread(str(conversation["conversation_id"]), "legacy-thread", "policy-v1")
    from stock_harness.chat_context import build_chat_context
    context = build_chat_context(
        store, symbol="000001.SZ", timeframe="daily", source_run_id=run.run_id,
    )
    turn = store.create_chat_turn(
        conversation_id=str(conversation["conversation_id"]), content="旧问题",
        template_id=None, context=context,
    )
    store.update_chat_turn(
        str(turn["turn_id"]), "completed", codex_turn_id="legacy-turn",
        assistant_content="旧回答",
    )
    store.close()

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.executescript("""
        CREATE TABLE legacy_ai_chat_conversations (
            conversation_id TEXT PRIMARY KEY,
            instrument_id INTEGER NOT NULL,
            timeframe TEXT NOT NULL,
            source_run_id TEXT NOT NULL,
            title TEXT NOT NULL,
            codex_thread_id TEXT,
            codex_policy_version TEXT,
            status TEXT NOT NULL,
            created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL
        );
        INSERT INTO legacy_ai_chat_conversations
        SELECT conversation_id, instrument_id, timeframe, source_run_id, title,
               codex_thread_id, codex_policy_version, status, created_at_ms, updated_at_ms
        FROM ai_chat_conversations;
        DROP TABLE ai_chat_conversations;
        ALTER TABLE legacy_ai_chat_conversations RENAME TO ai_chat_conversations;

        CREATE TABLE legacy_ai_chat_turn_contexts (
            turn_id TEXT PRIMARY KEY,
            schema_version TEXT NOT NULL,
            source_run_id TEXT NOT NULL,
            as_of_date INTEGER NOT NULL,
            input_digest TEXT NOT NULL,
            context_json TEXT NOT NULL
        ) WITHOUT ROWID;
        INSERT INTO legacy_ai_chat_turn_contexts
        SELECT turn_id, schema_version, source_run_id, as_of_date, input_digest, context_json
        FROM ai_chat_turn_contexts;
        DROP TABLE ai_chat_turn_contexts;
        ALTER TABLE legacy_ai_chat_turn_contexts RENAME TO ai_chat_turn_contexts;
    """)
    connection.close()

    migrated = SQLiteMarketDataStore(database)
    restored = migrated.get_chat_conversation(str(conversation["conversation_id"]))
    assert restored is not None
    assert restored["context_kind"] == "trend_analysis"
    assert restored["context_id"] == run.run_id
    assert restored["codex_thread_id"] == "legacy-thread"
    assert restored["turns"][0]["messages"][0]["content"] == "旧问题"
    assert restored["turns"][0]["messages"][1]["content"] == "旧回答"
    restored_context = migrated.get_chat_turn_context(str(turn["turn_id"]))
    assert restored_context is not None
    assert restored_context["source_run_id"] == run.run_id
    migrated.close()


def test_legacy_chat_session_migration_adds_missing_policy_column(tmp_path: Path) -> None:
    database = tmp_path / "legacy-chat-no-policy.sqlite3"
    store = SQLiteMarketDataStore(database)
    store.close()

    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.executescript("""
        CREATE TABLE legacy_ai_chat_conversations (
            conversation_id TEXT PRIMARY KEY,
            instrument_id INTEGER NOT NULL,
            timeframe TEXT NOT NULL,
            source_run_id TEXT NOT NULL,
            title TEXT NOT NULL,
            codex_thread_id TEXT,
            status TEXT NOT NULL,
            created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL,
            UNIQUE (instrument_id, timeframe, source_run_id)
        );
        DROP TABLE ai_chat_conversations;
        ALTER TABLE legacy_ai_chat_conversations RENAME TO ai_chat_conversations;
    """)
    connection.close()

    migrated = SQLiteMarketDataStore(database)
    columns = {
        str(row[1]) for row in migrated._connection.execute(
            "PRAGMA table_info(ai_chat_conversations)"
        )
    }
    table_sql = str(migrated._connection.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'ai_chat_conversations'"
    ).fetchone()[0])
    assert "codex_policy_version" in columns
    assert "UNIQUE (instrument_id, timeframe, source_run_id)" not in table_sql
    migrated.close()


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


def test_chat_conversations_support_multiple_sessions_and_management() -> None:
    store, run_id = _store_with_run()
    first = store.get_or_create_chat_conversation(
        symbol="000001.SZ", timeframe="daily", source_run_id=run_id
    )
    second = store.get_or_create_chat_conversation(
        symbol="000001.SZ", timeframe="daily", source_run_id=run_id, force_new=True
    )

    assert first["conversation_id"] != second["conversation_id"]
    assert len(store.list_chat_conversations(
        symbol="000001.SZ", timeframe="daily", source_run_id=run_id
    )) == 2
    renamed = store.update_chat_conversation(
        str(first["conversation_id"]), title="长期形态复盘", status="archived"
    )
    assert renamed is not None
    assert renamed["title"] == "长期形态复盘"
    assert renamed["status"] == "archived"
    assert store.delete_chat_conversation(str(first["conversation_id"])) is True
    assert store.get_chat_conversation(str(first["conversation_id"])) is None
    assert store.get_generated_analysis_run(run_id) is not None
    store.close()


def test_analysis_history_and_legacy_report_routes_preserve_revisions() -> None:
    store, run_id = _store_with_run()
    store.create_ai_analysis_report(
        symbol="000001.SZ", timeframe="daily", as_of_date=date(2026, 8, 31),
        source_run_id=run_id, title="旧版结论", conclusion_markdown="观察 [L1]",
        framework={"key_level_codes": ["L1"], "structures": [], "risk_reward": []},
        references=[{
            "code": "L1", "kind": "line", "label": "下降压力线",
            "detail": "旧报告证据", "analysis_item_id": "resistance",
        }], author="codex",
    )
    with TestClient(create_app(store, codex_bridge=FakeCodexBridge())) as client:
        runs = client.get("/api/analysis/trend/000001.SZ/runs").json()["items"]
        reports = client.get("/api/analysis/ai/000001.SZ/reports").json()["items"]

    assert runs[0]["run_id"] == run_id
    assert runs[0]["item_count"] == 2
    assert reports[0]["title"] == "旧版结论"
    assert reports[0]["source_run_id"] == run_id
    store.close()


def test_terminal_stream_reconnects_from_sqlite_after_memory_stream_is_lost() -> None:
    store, run_id = _store_with_run()
    bridge = FakeCodexBridge()
    with TestClient(create_app(store, codex_bridge=bridge)) as client:
        conversation = client.post("/api/ai/conversations", json={
            "symbol": "000001.SZ", "timeframe": "daily", "source_run_id": run_id,
        }).json()
        turn = client.post(
            f"/api/ai/conversations/{conversation['conversation_id']}/turns",
            json={"content": "分析", "template_id": None},
        ).json()
        first = client.get(f"/api/ai/turns/{turn['turn_id']}/events")
        client.app.state.chat_service._streams.clear()
        replay = client.get(
            f"/api/ai/turns/{turn['turn_id']}/events",
            headers={"Last-Event-ID": "2"},
        )

    assert "event: completed" in first.text
    assert "id: 3" in replay.text
    assert "event: delta" in replay.text or "event: completed" in replay.text
    store.close()


def test_allowed_mcp_tool_events_are_persisted_and_replayed() -> None:
    class ToolEventBridge(FakeCodexBridge):
        def run_turn(
            self,
            _thread_id: str,
            prompt: str,
            on_event: Callable[[str, dict[str, object]], None],
        ) -> tuple[str, str, str]:
            self.prompts.append(prompt)
            on_event("turn", {"turn_id": "codex-turn-tool"})
            event = {
                "item_id": "mcp-item-1",
                "server": "stock_harness_embedded",
                "tool": "get_daily_bars",
                "status": "inProgress",
                "arguments": {"symbol": "000001.SZ"},
            }
            on_event("tool-started", event)
            on_event("tool-completed", {
                **event, "status": "completed", "duration_ms": 18,
            })
            on_event("delta", {"delta": "done"})
            return "codex-turn-tool", "done", "completed"

    store, run_id = _store_with_run()
    with TestClient(create_app(store, codex_bridge=ToolEventBridge())) as client:
        conversation = client.post("/api/ai/conversations", json={
            "symbol": "000001.SZ", "timeframe": "daily", "source_run_id": run_id,
        }).json()
        turn = client.post(
            f"/api/ai/conversations/{conversation['conversation_id']}/turns",
            json={"content": "compare another instrument", "template_id": None},
        ).json()
        first = client.get(f"/api/ai/turns/{turn['turn_id']}/events")
        client.app.state.chat_service._streams.clear()
        replay = client.get(f"/api/ai/turns/{turn['turn_id']}/events")

    persisted = store.list_chat_stream_events(turn["turn_id"])
    assert [item["type"] for item in persisted if str(item["type"]).startswith("tool-")] == [
        "tool-started", "tool-completed",
    ]
    assert "event: tool-started" in first.text
    assert "event: tool-completed" in replay.text
    assert "stock_harness_embedded" in replay.text
    assert "000001.SZ" in replay.text
    store.close()


def test_pre_mcp_conversation_migrates_to_versioned_thread_with_bounded_history() -> None:
    class McpPolicyBridge(FakeCodexBridge):
        def __init__(self) -> None:
            super().__init__()
            self.started = 0
            self.resumed: list[str] = []

        def thread_policy_version(self) -> str:
            return "stockharness-embedded-mcp-v1"

        def start_thread(self, _workdir: Path) -> str:
            self.started += 1
            return "mcp-enabled-thread"

        def ensure_thread(self, thread_id: str, _workdir: Path) -> None:
            self.resumed.append(thread_id)

    store, run_id = _store_with_run()
    with TestClient(create_app(store, codex_bridge=FakeCodexBridge())) as client:
        conversation = client.post("/api/ai/conversations", json={
            "symbol": "000001.SZ", "timeframe": "daily", "source_run_id": run_id,
        }).json()
        first = client.post(
            f"/api/ai/conversations/{conversation['conversation_id']}/turns",
            json={"content": "first question", "template_id": None},
        ).json()
        client.get(f"/api/ai/turns/{first['turn_id']}/events")

    store.set_chat_codex_thread(
        conversation["conversation_id"], "pre-mcp-thread", None
    )
    bridge = McpPolicyBridge()
    with TestClient(create_app(store, codex_bridge=bridge)) as client:
        second = client.post(
            f"/api/ai/conversations/{conversation['conversation_id']}/turns",
            json={"content": "compare another symbol", "template_id": None},
        ).json()
        events = client.get(f"/api/ai/turns/{second['turn_id']}/events").text
        migrated = client.get(
            f"/api/ai/conversations/{conversation['conversation_id']}"
        ).json()

    assert bridge.started == 1
    assert bridge.resumed == []
    assert migrated["codex_thread_id"] == "mcp-enabled-thread"
    assert migrated["codex_policy_version"] == "stockharness-embedded-mcp-v1"
    assert "权限策略已升级" in events
    assert "<prior_conversation_history>" in bridge.prompts[0]
    assert "first question" in bridge.prompts[0]
    assert "compare another symbol" in bridge.prompts[0]
    store.close()


def test_pinned_codex_protocol_contract_matches_runtime_adapter() -> None:
    from stock_harness.codex_app_server import (
        ALLOWED_MCP_SERVER, ALLOWED_MCP_TOOLS, DENIED_ITEM_TYPES, SUPPORTED_VERSION,
    )

    contract = json.loads(
        Path("validation/codex-app-server-v0.146-contract.json").read_text(encoding="utf-8")
    )
    assert (
        contract["codex_cli"]["supported_major"],
        contract["codex_cli"]["supported_minor"],
    ) == SUPPORTED_VERSION
    assert set(contract["denied_item_types"]) == DENIED_ITEM_TYPES
    assert contract["allowed_mcp"] == {
        "server": ALLOWED_MCP_SERVER, "tools": sorted(ALLOWED_MCP_TOOLS),
    }
    assert contract["permission_profile"] == {
        "sandbox": "read-only", "approval_policy": "never",
        "mcp_enabled": True, "tool_event_tripwire": True,
    }


def test_embedded_mcp_rejects_non_root_loopback_url() -> None:
    with pytest.raises(ValueError, match="plain loopback HTTP URL"):
        CodexAppServerClient(mcp_api_url="http://127.0.0.1:8765/private")


def test_retry_reuses_the_original_immutable_context_after_market_correction() -> None:
    store, run_id = _store_with_run()
    bridge = FakeCodexBridge()
    with TestClient(create_app(store, codex_bridge=bridge)) as client:
        conversation = client.post("/api/ai/conversations", json={
            "symbol": "000001.SZ", "timeframe": "daily", "source_run_id": run_id,
        }).json()
        original = client.post(
            f"/api/ai/conversations/{conversation['conversation_id']}/turns",
            json={"content": "复核", "template_id": "correction"},
        ).json()
        client.get(f"/api/ai/turns/{original['turn_id']}/events")
        original_context = store.get_chat_turn_context(original["turn_id"])
        store.upsert_daily_bars("corrected", [
            DailyBar("000001.SZ", date(2026, 8, 31), 10.5, 99, 10, 11.5, 200),
        ])
        retried = client.post(f"/api/ai/turns/{original['turn_id']}/retry").json()
        retried_context = store.get_chat_turn_context(retried["turn_id"])

    assert retried_context == original_context
    store.close()


def test_protocol_tripwire_interrupts_a_denied_app_server_tool_item() -> None:
    class ProtocolFixture(CodexAppServerClient):
        def __init__(self) -> None:
            super().__init__(executable="fixture")
            self.calls: list[str] = []

        def _request(self, method: str, params: dict[str, object]) -> dict[str, object]:
            self.calls.append(method)
            if method == "turn/start":
                def emit() -> None:
                    self._listeners["thread-1"]("item/started", {
                        "threadId": "thread-1", "turnId": "turn-1",
                        "item": {"type": "commandExecution"},
                    })
                threading.Timer(0.01, emit).start()
                return {"turn": {"id": "turn-1"}}
            return {}

    client = ProtocolFixture()
    try:
        client.run_turn("thread-1", "do not use tools", lambda *_args: None)
        raise AssertionError("denied tool item was not rejected")
    except CodexUnavailableError as error:
        assert "commandExecution" in str(error)
    assert client.calls == ["turn/start", "turn/interrupt"]


def test_protocol_allows_only_stock_harness_mcp_items_and_normalizes_events() -> None:
    class ProtocolFixture(CodexAppServerClient):
        def __init__(self, server: str, tool: str) -> None:
            super().__init__(executable="fixture")
            self.server = server
            self.tool = tool
            self.calls: list[str] = []

        def _request(self, method: str, _params: dict[str, object]) -> dict[str, object]:
            self.calls.append(method)
            if method == "turn/start":
                def emit() -> None:
                    listener = self._listeners["thread-1"]
                    base = {
                        "threadId": "thread-1", "turnId": "turn-1",
                        "item": {
                            "id": "tool-1", "type": "mcpToolCall",
                            "server": self.server, "tool": self.tool,
                            "arguments": {"symbol": "000001.SZ", "secret": "hidden"},
                            "status": "inProgress",
                        },
                    }
                    listener("item/started", base)
                    base["item"] = {**base["item"], "status": "completed", "durationMs": 12}
                    listener("item/completed", base)
                    listener("turn/completed", {
                        "threadId": "thread-1",
                        "turn": {"id": "turn-1", "status": "completed"},
                    })
                threading.Timer(0.01, emit).start()
                return {"turn": {"id": "turn-1"}}
            return {}

    events: list[tuple[str, dict[str, object]]] = []
    allowed = ProtocolFixture("stock_harness_embedded", "get_daily_bars")
    assert allowed.run_turn(
        "thread-1", "compare", lambda kind, data: events.append((kind, data))
    )[2] == "completed"
    assert [kind for kind, _data in events if kind.startswith("tool-")] == [
        "tool-started", "tool-completed",
    ]
    assert events[-1][1].get("arguments") != {"secret": "hidden"}

    denied = ProtocolFixture("other_server", "get_daily_bars")
    try:
        denied.run_turn("thread-1", "compare", lambda *_args: None)
        raise AssertionError("non-StockHarness MCP server was not rejected")
    except CodexUnavailableError as error:
        assert "denied MCP" in str(error)
    assert denied.calls[-1] == "turn/interrupt"


def test_signal_chat_thread_configuration_excludes_recalculation(tmp_path: Path) -> None:
    from stock_harness.codex_app_server import (
        ALLOWED_MCP_SERVER, READ_ONLY_MCP_TOOLS, SIGNAL_ACCESS_PROFILE,
    )

    class ProtocolFixture(CodexAppServerClient):
        def __init__(self) -> None:
            super().__init__(executable="fixture")
            self.params: dict[str, object] = {}

        def _ensure_started(self) -> None:
            return None

        def _request(self, method: str, params: dict[str, object]) -> dict[str, object]:
            assert method == "thread/start"
            self.params = params
            return {"thread": {"id": "signal-thread"}}

    client = ProtocolFixture()
    thread_id = client.start_thread(tmp_path, SIGNAL_ACCESS_PROFILE)
    config = client.params["config"]
    assert isinstance(config, dict)
    server = config["mcp_servers"][ALLOWED_MCP_SERVER]
    enabled = server["enabled_tools"]
    assert enabled == sorted(READ_ONLY_MCP_TOOLS)
    assert "recalculate_trend_analysis" not in enabled
    assert server["command"]
    assert server["args"]
    assert server["cwd"] == str(Path(__file__).resolve().parents[1])
    assert server["env"]["STOCK_HARNESS_API_URL"] == "http://127.0.0.1:8765"
    assert "transport" not in server
    assert client.thread_policy_version(SIGNAL_ACCESS_PROFILE).endswith(":signal_run")
    assert thread_id == "signal-thread"


def test_signal_chat_tripwire_blocks_recalculation() -> None:
    from stock_harness.codex_app_server import SIGNAL_ACCESS_PROFILE

    class ProtocolFixture(CodexAppServerClient):
        def __init__(self) -> None:
            super().__init__(executable="fixture")
            self.calls: list[str] = []
            self._thread_profiles["thread-1"] = SIGNAL_ACCESS_PROFILE

        def _request(self, method: str, _params: dict[str, object]) -> dict[str, object]:
            self.calls.append(method)
            if method == "turn/start":
                def emit() -> None:
                    self._listeners["thread-1"]("item/started", {
                        "threadId": "thread-1", "turnId": "turn-1",
                        "item": {
                            "type": "mcpToolCall", "server": "stock_harness_embedded",
                            "tool": "recalculate_trend_analysis",
                        },
                    })
                threading.Timer(0.01, emit).start()
                return {"turn": {"id": "turn-1"}}
            return {}

    client = ProtocolFixture()
    with pytest.raises(CodexUnavailableError, match="denied MCP"):
        client.run_turn("thread-1", "recalculate", lambda *_args: None)
    assert client.calls[-1] == "turn/interrupt"


def test_position_and_risk_reward_templates_require_validated_structured_inputs() -> None:
    store, run_id = _store_with_run()
    with TestClient(create_app(store, codex_bridge=FakeCodexBridge())) as client:
        conversation = client.post("/api/ai/conversations", json={
            "symbol": "000001.SZ", "timeframe": "daily", "source_run_id": run_id,
        }).json()
        missing = client.post(
            f"/api/ai/conversations/{conversation['conversation_id']}/turns",
            json={"content": "计算", "template_id": "risk-reward"},
        )
        invalid = client.post(
            f"/api/ai/conversations/{conversation['conversation_id']}/turns",
            json={
                "content": "计算", "template_id": "risk-reward",
                "risk_reward": {
                    "direction": "long", "entry_price": 10,
                    "stop_price": 11, "target_price": 12,
                },
            },
        )
        valid = client.post(
            f"/api/ai/conversations/{conversation['conversation_id']}/turns",
            json={
                "content": "计算", "template_id": "risk-reward",
                "risk_reward": {
                    "direction": "long", "entry_price": 10,
                    "stop_price": 9, "target_price": 12,
                },
            },
        )
        context = store.get_chat_turn_context(valid.json()["turn_id"])

    assert missing.status_code == 422
    assert invalid.status_code == 422
    assert valid.status_code == 202
    assert context is not None
    assert context["user_inputs"]["risk_reward"]["stop_price"] == 9.0
    store.close()


def test_protocol_normalizes_reasoning_usage_and_completion_events() -> None:
    class ProtocolFixture(CodexAppServerClient):
        def __init__(self) -> None:
            super().__init__(executable="fixture")

        def _request(self, method: str, _params: dict[str, object]) -> dict[str, object]:
            if method == "turn/start":
                def emit() -> None:
                    listener = self._listeners["thread-1"]
                    listener("item/reasoning/summaryTextDelta", {
                        "threadId": "thread-1", "turnId": "turn-1", "delta": "检查证据",
                    })
                    listener("thread/tokenUsage/updated", {
                        "threadId": "thread-1", "turnId": "turn-1",
                        "tokenUsage": {"total": {"totalTokens": 42}},
                    })
                    listener("item/agentMessage/delta", {
                        "threadId": "thread-1", "turnId": "turn-1", "delta": "结论",
                    })
                    listener("turn/completed", {
                        "threadId": "thread-1", "turn": {"id": "turn-1", "status": "completed"},
                    })
                threading.Timer(0.01, emit).start()
                return {"turn": {"id": "turn-1"}}
            return {}

    events: list[tuple[str, dict[str, object]]] = []
    turn_id, response, status = ProtocolFixture().run_turn(
        "thread-1", "分析", lambda kind, data: events.append((kind, data))
    )
    assert (turn_id, response, status) == ("turn-1", "结论", "completed")
    assert ("reasoning", {"delta": "检查证据"}) in events
    assert any(kind == "usage" for kind, _data in events)


def test_jsonl_fake_app_server_covers_stream_malformed_crash_and_cancel(monkeypatch) -> None:
    fixture = str(Path("tests/fake_codex_app_server.py").resolve())

    for mode in ("normal", "malformed"):
        monkeypatch.setenv("STOCK_HARNESS_FAKE_CODEX_MODE", mode)
        client = CodexAppServerClient(
            executable=sys.executable, executable_args=(fixture,), request_timeout=2,
        )
        try:
            assert client.status()["authenticated"] is True
            thread_id = client.start_thread(Path(".tmp/fake-codex"))
            assert client.run_turn(thread_id, "test", lambda *_args: None)[1] == "fixture-ok"
        finally:
            client.close()

    monkeypatch.setenv("STOCK_HARNESS_FAKE_CODEX_MODE", "crash")
    crashed = CodexAppServerClient(
        executable=sys.executable, executable_args=(fixture,), request_timeout=2,
    )
    try:
        thread_id = crashed.start_thread(Path(".tmp/fake-codex"))
        try:
            crashed.run_turn(thread_id, "test", lambda *_args: None)
            raise AssertionError("transport crash did not fail the turn")
        except CodexUnavailableError as error:
            assert "exited during the turn" in str(error)
    finally:
        crashed.close()

    monkeypatch.setenv("STOCK_HARNESS_FAKE_CODEX_MODE", "hold")
    held = CodexAppServerClient(
        executable=sys.executable, executable_args=(fixture,), request_timeout=2,
    )
    started = threading.Event()
    result: list[tuple[str, str, str]] = []
    try:
        thread_id = held.start_thread(Path(".tmp/fake-codex"))
        worker = threading.Thread(target=lambda: result.append(held.run_turn(
            thread_id, "test",
            lambda kind, _data: started.set() if kind == "turn" else None,
        )))
        worker.start()
        assert started.wait(1)
        held.interrupt(thread_id, "fixture-turn")
        worker.join(2)
        assert result[0][2] == "interrupted"
    finally:
        held.close()


def test_codex_unavailable_does_not_block_health_or_analysis_history() -> None:
    class UnavailableBridge(FakeCodexBridge):
        def status(self) -> dict[str, object]:
            return {
                "available": False, "authenticated": False,
                "experimental": True, "error": "Codex unavailable",
            }

    store, run_id = _store_with_run()
    with TestClient(create_app(store, codex_bridge=UnavailableBridge())) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        history = client.get("/api/analysis/trend/000001.SZ/runs").json()["items"]
        status = client.get("/api/ai/codex/status").json()["codex"]

    assert history[0]["run_id"] == run_id
    assert status["available"] is False
    store.close()
