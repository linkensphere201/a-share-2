"""Deterministic JSONL fixture for Codex app-server transport tests."""

from __future__ import annotations

import json
import os
import sys


def send(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


if "--version" in sys.argv:
    print("codex-cli 0.146.0", flush=True)
    raise SystemExit(0)

mode = os.environ.get("STOCK_HARNESS_FAKE_CODEX_MODE", "normal")
for line in sys.stdin:
    request = json.loads(line)
    request_id = request.get("id")
    method = request.get("method")
    if request_id is None:
        continue
    if method == "initialize":
        send({"id": request_id, "result": {"serverInfo": {"name": "fixture"}}})
    elif method == "account/read":
        send({"id": request_id, "result": {"account": {"type": "fixture"}}})
    elif method in {"thread/start", "thread/resume"}:
        send({"id": request_id, "result": {"thread": {"id": "fixture-thread"}}})
    elif method == "turn/start":
        send({"id": request_id, "result": {"turn": {"id": "fixture-turn"}}})
        if mode == "malformed":
            print("this is not json", flush=True)
        if mode == "crash":
            raise SystemExit(9)
        if mode != "hold":
            send({"method": "item/agentMessage/delta", "params": {
                "threadId": "fixture-thread", "turnId": "fixture-turn", "delta": "fixture-ok",
            }})
            send({"method": "turn/completed", "params": {
                "threadId": "fixture-thread",
                "turn": {"id": "fixture-turn", "status": "completed"},
            }})
    elif method == "turn/interrupt":
        send({"id": request_id, "result": {}})
        send({"method": "turn/completed", "params": {
            "threadId": "fixture-thread",
            "turn": {"id": "fixture-turn", "status": "interrupted"},
        }})
    else:
        send({"id": request_id, "result": {}})
