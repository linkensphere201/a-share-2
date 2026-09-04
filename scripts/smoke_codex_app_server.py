"""Offline-safe handshake smoke for the supported local Codex app-server."""

from __future__ import annotations

import json

from stock_harness.codex_app_server import CodexAppServerClient


def main() -> int:
    client = CodexAppServerClient()
    try:
        status = client.status()
        print(json.dumps({
            "available": status.get("available"),
            "authenticated": status.get("authenticated"),
            "version": status.get("version"),
            "provider": status.get("provider"),
            "transport": status.get("transport"),
            "sandbox": status.get("sandbox"),
            "approval_policy": status.get("approval_policy"),
            "mcp_enabled": status.get("mcp_enabled"),
            "tool_event_tripwire": status.get("tool_event_tripwire"),
            "error": status.get("error"),
        }, ensure_ascii=False))
        return 0 if status.get("available") else 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
