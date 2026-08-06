import anyio
from mcp import Client

from stock_harness.mcp_server import build_server
from stock_harness.mcp_tools import StockHarnessMcpTools


class HealthApi:
    def get(self, path, params=None):
        assert path == "/api/health"
        return {"status": "ok"}


def test_mcp_protocol_lists_only_read_tools_and_calls_health():
    async def exercise():
        server = build_server(StockHarnessMcpTools(HealthApi()))
        async with Client(server, raise_exceptions=True) as client:
            listed = await client.list_tools()
            names = {tool.name for tool in listed.tools}
            assert names == {
                "stock_harness_health",
                "search_instruments",
                "get_instrument",
                "list_custom_groups",
                "get_custom_group",
                "get_daily_bars",
                "get_latest_quote",
                "list_instrument_members",
                "list_symbol_boards",
            }
            assert all(tool.annotations.read_only_hint for tool in listed.tools)
            assert all(tool.annotations.open_world_hint is False for tool in listed.tools)

            result = await client.call_tool("stock_harness_health", {})
            assert result.is_error is False
            assert result.structured_content["ok"] is True
            assert result.structured_content["data"] == {"status": "ok"}

    anyio.run(exercise)
