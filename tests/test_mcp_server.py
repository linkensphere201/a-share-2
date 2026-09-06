import anyio
import os
import subprocess
import sys
from pathlib import Path
from mcp import Client
import pytest

from stock_harness.mcp_server import EMBEDDED_CHAT_PROFILE, build_server
from stock_harness.mcp_tools import StockHarnessMcpTools


class HealthApi:
    def get(self, path, params=None):
        assert path == "/api/health"
        return {"status": "ok"}


class SlowHealthApi:
    def __init__(self):
        self.started = anyio.Event()
        self.finished = False

    def get(self, path, params=None):
        import time

        assert path == "/api/health"
        anyio.from_thread.run_sync(self.started.set)
        try:
            time.sleep(5)
            return {"status": "ok"}
        finally:
            self.finished = True


def test_mcp_protocol_exposes_one_bounded_local_write_tool_and_calls_health():
    async def exercise():
        server = build_server(StockHarnessMcpTools(HealthApi()))
        async with Client(server, raise_exceptions=True) as client:
            listed = await client.list_tools()
            names = {tool.name for tool in listed.tools}
            assert names == {
                "stock_harness_health",
                "get_active_workspace",
                "search_instruments",
                "get_instrument",
                "list_custom_groups",
                "get_custom_group",
                "get_daily_bars",
                "get_latest_quote",
                "list_futures_coverage",
                "get_futures_continuous",
                "get_trend_analysis",
                "get_ai_analysis",
                "save_ai_analysis",
                "list_instrument_members",
                "list_symbol_boards",
                "list_signal_definitions",
                "list_signal_runs",
                "get_signal_run",
                "get_signal_item",
            }
            by_name = {tool.name: tool for tool in listed.tools}
            assert by_name["save_ai_analysis"].annotations.read_only_hint is False
            assert by_name["save_ai_analysis"].annotations.destructive_hint is False
            assert all(
                tool.annotations.read_only_hint
                for tool in listed.tools if tool.name != "save_ai_analysis"
            )
            assert all(tool.annotations.open_world_hint is False for tool in listed.tools)
            assert not any(
                token in name
                for name in names
                for token in ("order", "account", "position", "provider", "sql", "credential")
            )

            result = await client.call_tool("stock_harness_health", {})
            assert result.is_error is False
            assert result.structured_content["ok"] is True
            assert result.structured_content["data"] == {"status": "ok"}

    anyio.run(exercise)


def test_mcp_protocol_rejects_malformed_arguments_before_api_access():
    class NoAccessApi:
        def get(self, path, params=None):
            raise AssertionError(f"API must not be called: {path}")

    async def exercise():
        server = build_server(StockHarnessMcpTools(NoAccessApi()))
        async with Client(server, raise_exceptions=False) as client:
            result = await client.call_tool(
                "get_daily_bars",
                {"symbol": "000001.SZ", "max_bars": 8001},
            )
            assert result.is_error is True

    anyio.run(exercise)


def test_embedded_chat_profile_replaces_report_write_with_controlled_recalculation():
    async def exercise():
        server = build_server(
            StockHarnessMcpTools(HealthApi()), profile=EMBEDDED_CHAT_PROFILE
        )
        async with Client(server, raise_exceptions=True) as client:
            listed = await client.list_tools()
            by_name = {tool.name: tool for tool in listed.tools}
            assert "save_ai_analysis" not in by_name
            assert "recalculate_trend_analysis" in by_name
            compute = by_name["recalculate_trend_analysis"].annotations
            assert compute.read_only_hint is False
            assert compute.destructive_hint is False
            assert compute.idempotent_hint is True
            assert all(tool.annotations.open_world_hint is False for tool in listed.tools)

    anyio.run(exercise)


def test_unknown_mcp_profile_is_rejected():
    with pytest.raises(ValueError, match="unsupported StockHarness MCP profile"):
        build_server(StockHarnessMcpTools(HealthApi()), profile="unknown")


def test_mcp_call_cancellation_releases_protocol_task_without_waiting_for_http_timeout():
    async def exercise():
        api = SlowHealthApi()
        server = build_server(StockHarnessMcpTools(api))
        with anyio.fail_after(1):
            async with Client(server, raise_exceptions=True) as client:
                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(client.call_tool, "stock_harness_health", {})
                    await api.started.wait()
                    tasks.cancel_scope.cancel()

    anyio.run(exercise)


def test_stdio_server_exits_after_client_input_closes():
    project_root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(project_root / "src")}
    process = subprocess.Popen(
        [sys.executable, "-m", "stock_harness.mcp_server"],
        cwd=project_root,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    process.stdin.close()
    assert process.wait(timeout=5) == 0
