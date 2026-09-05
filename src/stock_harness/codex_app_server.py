"""Small version-pinned adapter for the experimental Codex app-server protocol."""

from __future__ import annotations

from concurrent.futures import Future
import json
import logging
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable
from urllib.parse import urlparse

from stock_harness.ai_provider import AiConversationProvider


LOGGER = logging.getLogger(__name__)
SUPPORTED_VERSION = (0, 146)
DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "apps", "browser_use", "computer_use",
    "multi_agent", "image_generation",
)
DENIED_ITEM_TYPES = {
    "commandExecution", "fileChange", "webSearch", "imageView",
    "computerAction", "dynamicToolCall", "collabAgentToolCall",
}
ALLOWED_MCP_SERVER = "stock_harness_embedded"
ALLOWED_MCP_TOOLS = frozenset({
    "stock_harness_health", "get_active_workspace", "search_instruments",
    "get_instrument", "list_custom_groups", "get_custom_group",
    "get_daily_bars", "get_latest_quote", "list_futures_coverage",
    "get_futures_continuous", "get_trend_analysis", "get_ai_analysis",
    "list_instrument_members", "list_symbol_boards", "recalculate_trend_analysis",
})


class CodexUnavailableError(RuntimeError):
    pass


CodexBridge = AiConversationProvider


class CodexAppServerClient:
    def __init__(
        self, executable: str | None = None, request_timeout: float = 20.0,
        executable_args: tuple[str, ...] = (),
        mcp_api_url: str = "http://127.0.0.1:8765",
    ) -> None:
        self._executable = executable
        self._executable_args = executable_args
        self._request_timeout = request_timeout
        self._mcp_api_url = _loopback_url(mcp_api_url)
        self._process: subprocess.Popen[str] | None = None
        self._pending: dict[int, Future[dict[str, object]]] = {}
        self._pending_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._request_id = 0
        self._start_lock = threading.Lock()
        self._version: str | None = None
        self._loaded_threads: set[str] = set()
        self._listeners: dict[str, Callable[[str, dict[str, object]], None]] = {}
        self._listeners_lock = threading.Lock()
        self._start_attempts: list[float] = []
        self._restart_count = 0

    def status(self) -> dict[str, object]:
        try:
            self._ensure_started()
            account = self._request("account/read", {})
            signed_in = account.get("account") is not None or not bool(
                account.get("requiresOpenaiAuth", True)
            )
            return {
                "available": True, "authenticated": signed_in,
                "version": self._version, "experimental": True,
                "provider": "local-codex", "model": None,
                "process_running": self._process is not None and self._process.poll() is None,
                "transport": "stdio-jsonl", "sandbox": "read-only",
                "approval_policy": "never", "mcp_enabled": True,
                "mcp_server": ALLOWED_MCP_SERVER,
                "mcp_tools": sorted(ALLOWED_MCP_TOOLS),
                "builtin_tools_disabled": list(DISABLED_FEATURES),
                "tool_event_tripwire": True, "restart_count": self._restart_count,
                "error": None if signed_in else "Codex 尚未登录",
            }
        except Exception as error:
            return {
                "available": False, "authenticated": False,
                "version": self._version, "experimental": True,
                "provider": "local-codex", "model": None,
                "process_running": False, "transport": "stdio-jsonl",
                "sandbox": "read-only", "approval_policy": "never",
                "mcp_enabled": True, "mcp_server": ALLOWED_MCP_SERVER,
                "mcp_tools": sorted(ALLOWED_MCP_TOOLS),
                "builtin_tools_disabled": list(DISABLED_FEATURES),
                "tool_event_tripwire": True, "restart_count": self._restart_count,
                "error": _bounded_error(error),
            }

    def start_thread(self, workdir: Path) -> str:
        self._ensure_started()
        workdir.mkdir(parents=True, exist_ok=True)
        result = self._request("thread/start", {
            "cwd": str(workdir.resolve()),
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "ephemeral": False,
            "baseInstructions": (
                "You are the embedded StockHarness market-analysis assistant. "
                "Use the frozen selected-result context and, when needed, only the allowlisted "
                "stock_harness MCP tools. Never call shell, filesystem, browser, web, apps, "
                "other MCP servers, or request permissions. The only permitted computation "
                "write is recalculate_trend_analysis over already stored market bars."
            ),
            "developerInstructions": (
                "Answer in Chinese. Keep facts, rule-based inference, and uncertainty distinct. "
                "This is research support, not an order or guaranteed return."
            ),
        })
        thread = result.get("thread")
        if not isinstance(thread, dict) or not thread.get("id"):
            raise CodexUnavailableError("Codex did not return a thread ID")
        thread_id = str(thread["id"])
        self._loaded_threads.add(thread_id)
        return thread_id

    def ensure_thread(self, thread_id: str, workdir: Path) -> None:
        self._ensure_started()
        if thread_id in self._loaded_threads:
            return
        self._request("thread/resume", {
            "threadId": thread_id,
            "cwd": str(workdir.resolve()),
            "approvalPolicy": "never",
            "sandbox": "read-only",
        })
        self._loaded_threads.add(thread_id)

    def run_turn(
        self, thread_id: str, prompt: str, on_event: Callable[[str, dict[str, object]], None]
    ) -> tuple[str, str, str]:
        events: queue.Queue[tuple[str, dict[str, object]]] = queue.Queue()

        def listener(method: str, params: dict[str, object]) -> None:
            if method == "transport/closed" or params.get("threadId") == thread_id:
                events.put((method, params))

        with self._listeners_lock:
            if thread_id in self._listeners:
                raise RuntimeError("Codex thread already has an active turn")
            self._listeners[thread_id] = listener
        try:
            result = self._request("turn/start", {
                "threadId": thread_id,
                "input": [{"type": "text", "text": prompt}],
                "approvalPolicy": "never",
            })
            turn = result.get("turn")
            if not isinstance(turn, dict) or not turn.get("id"):
                raise CodexUnavailableError("Codex did not return a turn ID")
            turn_id = str(turn["id"])
            on_event("turn", {"turn_id": turn_id})
            parts: list[str] = []
            deadline = time.monotonic() + 600
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CodexUnavailableError("Codex turn exceeded 10 minutes")
                method, params = events.get(timeout=min(300, remaining))
                event_turn_id = params.get("turnId")
                nested_turn = params.get("turn")
                if event_turn_id not in {None, turn_id}:
                    continue
                if isinstance(nested_turn, dict) and nested_turn.get("id") != turn_id:
                    continue
                if method == "item/agentMessage/delta":
                    delta = str(params.get("delta", ""))
                    if delta:
                        parts.append(delta)
                        on_event("delta", {"delta": delta})
                elif method in {
                    "item/reasoning/summaryTextDelta", "item/reasoning/textDelta",
                }:
                    delta = str(params.get("delta", ""))
                    if delta:
                        on_event("reasoning", {"delta": delta})
                elif method == "thread/tokenUsage/updated":
                    usage = params.get("tokenUsage")
                    on_event("usage", {"usage": usage if isinstance(usage, dict) else {}})
                elif method in {"item/started", "item/completed"}:
                    item = params.get("item")
                    item_type = str(item.get("type", "")) if isinstance(item, dict) else ""
                    if item_type == "mcpToolCall":
                        if not _allowed_mcp_item(item):
                            self.interrupt(thread_id, turn_id)
                            on_event("warning", {
                                "message": "Codex MCP call was blocked by the StockHarness allow list",
                                "item_type": item_type,
                            })
                            raise CodexUnavailableError("Codex attempted a denied MCP tool call")
                        on_event(
                            "tool-started" if method == "item/started" else "tool-completed",
                            _mcp_event(item),
                        )
                        continue
                    if item_type in DENIED_ITEM_TYPES:
                        self.interrupt(thread_id, turn_id)
                        on_event("warning", {
                            "message": "已阻止超出只读分析权限的 Codex 工具调用",
                            "item_type": item_type,
                        })
                        raise CodexUnavailableError(
                            f"Codex attempted denied tool item: {item_type}"
                        )
                    if item_type and item_type not in {"agentMessage", "reasoning", "plan"}:
                        on_event(
                            "tool-started" if method == "item/started" else "tool-completed",
                            {"item_type": item_type},
                        )
                elif method == "turn/completed":
                    status = str(nested_turn.get("status", "completed")) if isinstance(nested_turn, dict) else "completed"
                    return turn_id, "".join(parts), status
                elif method == "error":
                    on_event("warning", {"message": "Codex 返回错误事件"})
                elif method == "transport/closed":
                    raise CodexUnavailableError("Codex app-server exited during the turn")
        except queue.Empty as error:
            raise CodexUnavailableError("Codex response timed out") from error
        finally:
            with self._listeners_lock:
                self._listeners.pop(thread_id, None)

    def interrupt(self, thread_id: str, turn_id: str) -> None:
        self._request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        self._fail_pending(CodexUnavailableError("Codex app-server stopped"))

    def _ensure_started(self) -> None:
        with self._start_lock:
            if self._process is not None and self._process.poll() is None:
                return
            now = time.monotonic()
            self._start_attempts = [value for value in self._start_attempts if now - value < 60]
            if len(self._start_attempts) >= 3:
                raise CodexUnavailableError("Codex app-server repeatedly exited; retry after 60 seconds")
            if self._process is not None:
                self._restart_count += 1
            self._start_attempts.append(now)
            executable = self._executable or shutil.which("codex.exe") or shutil.which("codex.cmd")
            if not executable:
                raise CodexUnavailableError("未找到本机 Codex CLI")
            version_result = subprocess.run(
                [executable, *self._executable_args, "--version"],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=10, check=False,
            )
            self._version = version_result.stdout.strip()
            match = re.search(r"(\d+)\.(\d+)\.(\d+)", self._version)
            if not match or tuple(map(int, match.groups()[:2])) != SUPPORTED_VERSION:
                raise CodexUnavailableError(
                    f"Codex 版本不兼容：{self._version or 'unknown'}；需要 0.146.x"
                )
            disabled_servers = _configured_mcp_servers(
                executable, self._executable_args
            ) if _is_codex_executable(executable) else []
            command = [
                executable, *self._executable_args,
                "app-server", "--stdio", *self._mcp_config_args(disabled_servers),
            ]
            for feature in DISABLED_FEATURES:
                command.extend(["--disable", feature])
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
            self._loaded_threads.clear()
            threading.Thread(target=self._read_stdout, name="codex-jsonl", daemon=True).start()
            threading.Thread(target=self._read_stderr, name="codex-stderr", daemon=True).start()
            self._request("initialize", {
                "clientInfo": {"name": "stock-harness", "title": "StockHarness", "version": "0.1.0"},
                "capabilities": {"experimentalApi": False},
            })
            self._notify("initialized", {})

    def _mcp_config_args(self, disabled_servers: list[str] | None = None) -> list[str]:
        command, args, cwd = _embedded_mcp_command()
        mcp_environment = "{ " + ", ".join([
            f"STOCK_HARNESS_API_URL = {_toml_string(self._mcp_api_url)}",
            'STOCK_HARNESS_MCP_TIMEOUT_SECONDS = "30"',
            'STOCK_HARNESS_MCP_LOG_LEVEL = "WARNING"',
            'STOCK_HARNESS_MCP_PROFILE = "embedded-chat"',
        ]) + " }"
        values = {
            f"mcp_servers.{ALLOWED_MCP_SERVER}.command": _toml_string(command),
            f"mcp_servers.{ALLOWED_MCP_SERVER}.args": json.dumps(args),
            f"mcp_servers.{ALLOWED_MCP_SERVER}.cwd": _toml_string(cwd),
            f"mcp_servers.{ALLOWED_MCP_SERVER}.required": "true",
            f"mcp_servers.{ALLOWED_MCP_SERVER}.startup_timeout_sec": "15",
            f"mcp_servers.{ALLOWED_MCP_SERVER}.tool_timeout_sec": "120",
            f"mcp_servers.{ALLOWED_MCP_SERVER}.enabled_tools": json.dumps(
                sorted(ALLOWED_MCP_TOOLS)
            ),
            f"mcp_servers.{ALLOWED_MCP_SERVER}.env": mcp_environment,
        }
        result: list[str] = []
        for key, value in values.items():
            result.extend(["-c", f"{key}={value}"])
        for name in disabled_servers or []:
            if name != ALLOWED_MCP_SERVER:
                result.extend([
                    "-c", f"mcp_servers.{_toml_key_segment(name)}.enabled=false"
                ])
        return result

    def _request(self, method: str, params: dict[str, object]) -> dict[str, object]:
        process = self._process
        if process is None or process.poll() is not None:
            if method != "initialize":
                self._ensure_started()
                process = self._process
        assert process is not None
        future: Future[dict[str, object]] = Future()
        with self._pending_lock:
            self._request_id += 1
            request_id = self._request_id
            self._pending[request_id] = future
        self._write({"id": request_id, "method": method, "params": params})
        try:
            return future.result(timeout=self._request_timeout)
        except TimeoutError as error:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise CodexUnavailableError(f"Codex request timed out: {method}") from error

    def _notify(self, method: str, params: dict[str, object]) -> None:
        self._write({"method": method, "params": params})

    def _write(self, payload: dict[str, object]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise CodexUnavailableError("Codex app-server is not running")
        with self._write_lock:
            process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            process.stdin.flush()

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    LOGGER.warning("codex_protocol_invalid_json")
                    continue
                request_id = payload.get("id")
                method = payload.get("method")
                if request_id is not None and method is None:
                    with self._pending_lock:
                        future = self._pending.pop(int(request_id), None)
                    if future is not None:
                        if payload.get("error") is not None:
                            future.set_exception(CodexUnavailableError(str(payload["error"])))
                        else:
                            future.set_result(dict(payload.get("result") or {}))
                elif request_id is not None and method is not None:
                    self._write({
                        "id": request_id,
                        "error": {"code": -32001, "message": "StockHarness denies all server requests"},
                    })
                    LOGGER.warning("codex_server_request_denied method=%s", method)
                elif method is not None:
                    params = dict(payload.get("params") or {})
                    with self._listeners_lock:
                        listener = self._listeners.get(str(params.get("threadId", "")))
                    if listener is not None:
                        listener(str(method), params)
        finally:
            self._fail_pending(CodexUnavailableError("Codex app-server exited"))
            with self._listeners_lock:
                listeners = list(self._listeners.values())
            for listener in listeners:
                listener("transport/closed", {})

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        last_log = 0.0
        for line in process.stderr:
            now = time.monotonic()
            if now - last_log >= 30:
                LOGGER.warning("codex_app_server_stderr message=%s", " ".join(line.split())[:300])
                last_log = now

    def _fail_pending(self, error: Exception) -> None:
        with self._pending_lock:
            pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(error)


def _bounded_error(error: Exception) -> str:
    return " ".join(str(error).split())[:300] or type(error).__name__


def _loopback_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("embedded MCP API URL must be a plain loopback HTTP URL")
    return value.rstrip("/")


def _embedded_mcp_command() -> tuple[str, list[str], str]:
    if getattr(sys, "frozen", False):
        return sys.executable, ["--mcp-server"], str(Path(sys.executable).resolve().parent)
    return (
        sys.executable,
        ["-m", "stock_harness.mcp_server"],
        str(Path(__file__).resolve().parents[2]),
    )


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_key_segment(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return value
    raise CodexUnavailableError("Configured MCP server name cannot be safely overridden")


def _is_codex_executable(value: str) -> bool:
    return Path(value).name.lower() in {"codex", "codex.exe", "codex.cmd"}


def _configured_mcp_servers(
    executable: str, executable_args: tuple[str, ...]
) -> list[str]:
    try:
        result = subprocess.run(
            [executable, *executable_args, "mcp", "list", "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, check=False,
        )
        if result.returncode != 0:
            raise CodexUnavailableError("Codex MCP inventory command failed")
        payload = json.loads(result.stdout)
        if not isinstance(payload, list):
            raise CodexUnavailableError("Codex MCP inventory returned an invalid response")
        return [
            str(item["name"])
            for item in payload
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ]
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        LOGGER.warning("codex_mcp_inventory_failed")
        raise CodexUnavailableError(
            "Could not inventory configured MCP servers; embedded Chat remains disabled"
        ) from error


def _allowed_mcp_item(item: dict[str, object]) -> bool:
    return (
        str(item.get("server", "")) == ALLOWED_MCP_SERVER
        and str(item.get("tool", "")) in ALLOWED_MCP_TOOLS
    )


def _mcp_event(item: dict[str, object]) -> dict[str, object]:
    arguments = item.get("arguments")
    safe_arguments: dict[str, object] = {}
    if isinstance(arguments, dict):
        for key in ("symbol", "query", "timeframe", "classification"):
            value = arguments.get(key)
            if isinstance(value, (str, int, float, bool)):
                safe_arguments[key] = str(value)[:200] if isinstance(value, str) else value
    error = item.get("error")
    return {
        "item_id": str(item.get("id", ""))[:100],
        "server": ALLOWED_MCP_SERVER,
        "tool": str(item.get("tool", "")),
        "status": str(item.get("status", "")),
        "duration_ms": item.get("durationMs"),
        "arguments": safe_arguments,
        "error": " ".join(str(error).split())[:240] if error else None,
    }
