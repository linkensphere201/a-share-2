"""Application-owned orchestration for immutable result-bound Codex chat."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import threading
from typing import Iterator

from stock_harness.chat_context import build_chat_context, render_chat_prompt
from stock_harness.codex_app_server import CodexBridge
from stock_harness.sqlite_store import SQLiteMarketDataStore


LOGGER = logging.getLogger(__name__)

CHAT_TEMPLATES: tuple[dict[str, str], ...] = (
    {"id": "daily-update", "label": "每日跟踪", "instruction": "对比并解释当前形态状态，指出延续、变化、失效和需要次日跟踪的证据。"},
    {"id": "correction", "label": "纠错", "instruction": "严格审查形态、趋势线和关键位是否成立，优先指出证据不足、冲突和算法可能误判。"},
    {"id": "entry", "label": "开仓点", "instruction": "给出条件式触发位、确认条件、失效位、上方阻力和不入场情形，不生成交易指令。"},
    {"id": "position-tracking", "label": "持仓跟踪", "instruction": "基于用户提供的持仓信息分析趋势是否延续、何处失效以及需要跟踪的证据。"},
    {"id": "exit", "label": "止盈止损", "instruction": "区分结构失效、保护性止损和情景止盈，明确各自依据和不确定性。"},
    {"id": "risk-reward", "label": "盈亏比", "instruction": "只使用用户明确给出的或快照可验证的入场、止损、目标价格计算盈亏比，并列出假设。"},
)


class TurnEventStream:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._events: list[dict[str, object]] = []
        self._terminal = False

    def publish(self, event_type: str, data: dict[str, object]) -> None:
        with self._condition:
            self._events.append({
                "sequence": len(self._events) + 1,
                "type": event_type,
                "data": data,
            })
            if event_type in {"completed", "failed", "cancelled"}:
                self._terminal = True
            self._condition.notify_all()

    def iterate(self, after: int = 0) -> Iterator[str]:
        cursor = max(0, after)
        while True:
            with self._condition:
                if cursor >= len(self._events) and not self._terminal:
                    self._condition.wait(timeout=15)
                pending = self._events[cursor:]
                terminal = self._terminal
            if not pending:
                if terminal:
                    return
                yield ": keepalive\n\n"
                continue
            for event in pending:
                cursor = int(event["sequence"])
                yield (
                    f"id: {cursor}\n"
                    f"event: {event['type']}\n"
                    f"data: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
                )
            if terminal and cursor >= len(self._events):
                return


class CodexChatService:
    def __init__(
        self, store: SQLiteMarketDataStore, bridge: CodexBridge, workdir: Path
    ) -> None:
        self._store = store
        self._bridge = bridge
        self._workdir = workdir
        self._streams: dict[str, TurnEventStream] = {}
        self._active: dict[str, tuple[str, str | None]] = {}
        self._workers: set[threading.Thread] = set()
        self._lock = threading.Lock()
        recovered = self._store.fail_interrupted_chat_turns()
        if recovered:
            LOGGER.warning("codex_chat_interrupted_turns_recovered count=%s", recovered)

    def capabilities(self) -> dict[str, object]:
        return {"codex": self._bridge.status(), "templates": list(CHAT_TEMPLATES)}

    def conversation(
        self, *, symbol: str, timeframe: str, source_run_id: str
    ) -> dict[str, object]:
        return self._store.get_or_create_chat_conversation(
            symbol=symbol, timeframe=timeframe, source_run_id=source_run_id
        )

    def get_conversation(self, conversation_id: str) -> dict[str, object] | None:
        return self._store.get_chat_conversation(conversation_id)

    def start_turn(
        self,
        *,
        conversation_id: str,
        content: str,
        template_id: str | None,
    ) -> dict[str, object]:
        conversation = self._store.get_chat_conversation(conversation_id)
        if conversation is None:
            raise ValueError("chat conversation not found")
        template = next((item for item in CHAT_TEMPLATES if item["id"] == template_id), None)
        if template_id is not None and template is None:
            raise ValueError("unknown chat template")
        context = build_chat_context(
            self._store,
            symbol=str(conversation["symbol"]),
            timeframe=str(conversation["timeframe"]),
            source_run_id=str(conversation["source_run_id"]),
        )
        turn = self._store.create_chat_turn(
            conversation_id=conversation_id, content=content,
            template_id=template_id, context=context,
        )
        stream = TurnEventStream()
        with self._lock:
            self._streams[str(turn["turn_id"])] = stream
        stream.publish("queued", {"turn_id": turn["turn_id"]})
        worker = threading.Thread(
            target=self._run_turn,
            args=(conversation_id, turn, content, context, template),
            name=f"codex-turn-{str(turn['turn_id'])[:8]}", daemon=True,
        )
        with self._lock:
            self._workers.add(worker)
        worker.start()
        return turn

    def events(self, turn_id: str, after: int = 0) -> Iterator[str]:
        with self._lock:
            stream = self._streams.get(turn_id)
        if stream is None:
            raise ValueError("chat turn event stream not found")
        return stream.iterate(after)

    def cancel(self, turn_id: str) -> None:
        with self._lock:
            active = self._active.get(turn_id)
            stream = self._streams.get(turn_id)
        if active is None or active[1] is None:
            raise ValueError("chat turn is not cancellable")
        self._bridge.interrupt(active[0], active[1])
        if stream is not None:
            stream.publish("status", {"message": "正在停止"})

    def close(self) -> None:
        self._bridge.close()
        with self._lock:
            workers = list(self._workers)
        for worker in workers:
            worker.join(timeout=2)

    def _run_turn(
        self,
        conversation_id: str,
        turn: dict[str, object],
        content: str,
        context: dict[str, object],
        template: dict[str, str] | None,
    ) -> None:
        turn_id = str(turn["turn_id"])
        with self._lock:
            stream = self._streams[turn_id]
        parts: list[str] = []
        try:
            conversation = self._store.get_chat_conversation(conversation_id)
            assert conversation is not None
            codex_thread_id = conversation.get("codex_thread_id")
            if not codex_thread_id:
                codex_thread_id = self._bridge.start_thread(self._workdir)
                self._store.set_chat_codex_thread(conversation_id, str(codex_thread_id))
            else:
                self._bridge.ensure_thread(str(codex_thread_id), self._workdir)
            self._store.update_chat_turn(turn_id, "running")
            stream.publish("started", {"turn_id": turn_id})
            prompt = render_chat_prompt(
                context, content, template["instruction"] if template else None
            )

            def on_event(event_type: str, data: dict[str, object]) -> None:
                if event_type == "delta":
                    parts.append(str(data.get("delta", "")))
                elif event_type == "turn":
                    codex_turn = str(data.get("turn_id", ""))
                    with self._lock:
                        self._active[turn_id] = (str(codex_thread_id), codex_turn)
                    self._store.update_chat_turn(
                        turn_id, "running", codex_turn_id=codex_turn
                    )
                stream.publish(event_type, data)

            with self._lock:
                self._active[turn_id] = (str(codex_thread_id), None)
            codex_turn_id, response, codex_status = self._bridge.run_turn(
                str(codex_thread_id), prompt, on_event
            )
            with self._lock:
                self._active[turn_id] = (str(codex_thread_id), codex_turn_id)
            status = "cancelled" if codex_status in {"cancelled", "interrupted"} else (
                "completed" if codex_status == "completed" else "failed"
            )
            final_text = response or "".join(parts)
            error = None if status == "completed" else f"Codex turn ended as {codex_status}"
            self._store.update_chat_turn(
                turn_id, status, codex_turn_id=codex_turn_id,
                error=error, assistant_content=final_text or None,
            )
            stream.publish(status, {"turn_id": turn_id, "status": codex_status})
        except Exception as error:
            message = " ".join(str(error).split())[:500] or type(error).__name__
            LOGGER.warning("codex_chat_turn_failed turn_id=%s error=%s", turn_id, message)
            self._store.update_chat_turn(
                turn_id, "failed", error=message,
                assistant_content="".join(parts) or None,
            )
            stream.publish("failed", {"turn_id": turn_id, "message": message})
        finally:
            with self._lock:
                self._active.pop(turn_id, None)
                self._workers.discard(threading.current_thread())
