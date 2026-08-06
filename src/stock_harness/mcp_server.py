"""StockHarness read-only MCP server entry point."""

from __future__ import annotations

import logging
import os
import sys
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from stock_harness.mcp_tools import LocalStockHarnessApi, StockHarnessMcpTools


READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
INSTRUCTIONS = (
    "StockHarness is a local, read-only A-share research source. Always report symbol, "
    "effective date, source, and final/intraday state from tool results. Treat intraday "
    "bars as provisional. Never infer trading authorization or attempt mutation."
)


def build_server(tools: StockHarnessMcpTools | None = None) -> MCPServer:
    service = tools or StockHarnessMcpTools(
        LocalStockHarnessApi(
            os.environ.get("STOCK_HARNESS_API_URL", "http://127.0.0.1:8001"),
            float(os.environ.get("STOCK_HARNESS_MCP_TIMEOUT_SECONDS", "5")),
        )
    )
    server = MCPServer("StockHarness", instructions=INSTRUCTIONS)

    @server.tool(title="Check StockHarness availability", annotations=READ_ONLY)
    def stock_harness_health() -> dict[str, object]:
        """Check whether the running local StockHarness APP API is available."""
        return service.health()

    @server.tool(title="Search StockHarness instruments", annotations=READ_ONLY)
    def search_instruments(
        query: Annotated[str, Field(max_length=100)] = "",
        classification: Literal["stock", "etf", "index", "concept", "industry", "sector"] | None = None,
        source_system: Annotated[str | None, Field(max_length=40)] = None,
        family: Annotated[str | None, Field(max_length=80)] = None,
        category: Annotated[str | None, Field(max_length=80)] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        offset: Annotated[int, Field(ge=0, le=100_000)] = 0,
    ) -> dict[str, object]:
        """Search stocks, ETFs, indices, boards, and custom groups by name, symbol, or pinyin."""
        return service.search_instruments(
            query, classification, source_system, family, category, limit, offset
        )

    @server.tool(title="Get instrument metadata", annotations=READ_ONLY)
    def get_instrument(
        symbol: Annotated[str, Field(min_length=1, max_length=200)]
    ) -> dict[str, object]:
        """Get one canonical instrument's identity, catalog, coverage, and open incidents."""
        return service.get_instrument(symbol)

    @server.tool(title="List custom groups", annotations=READ_ONLY)
    def list_custom_groups(
        query: Annotated[str, Field(max_length=100)] = "",
        limit: Annotated[int, Field(ge=1, le=200)] = 100,
    ) -> dict[str, object]:
        """List user-defined StockHarness groups and their latest summary metrics."""
        return service.list_custom_groups(query, limit)

    @server.tool(title="Get custom group", annotations=READ_ONLY)
    def get_custom_group(
        group_id: Annotated[str, Field(min_length=1, max_length=100)],
        max_members: Annotated[int, Field(ge=1, le=500)] = 500,
    ) -> dict[str, object]:
        """Get an ordered custom group with member roles, tags, notes, and availability."""
        return service.get_custom_group(group_id, max_members)

    @server.tool(title="Get daily OHLCV bars", annotations=READ_ONLY)
    def get_daily_bars(
        symbol: Annotated[str, Field(min_length=1, max_length=200)],
        start_date: Annotated[str | None, Field(description="Inclusive YYYY-MM-DD date.")] = None,
        end_date: Annotated[str | None, Field(description="Inclusive YYYY-MM-DD date.")] = None,
        max_bars: Annotated[int, Field(ge=1, le=8000)] = 8000,
    ) -> dict[str, object]:
        """Read bounded daily OHLCV with canonical/provisional fusion and explicit bar state."""
        return service.get_daily_bars(symbol, start_date, end_date, max_bars)

    @server.tool(title="Get latest quote state", annotations=READ_ONLY)
    def get_latest_quote(
        symbol: Annotated[str, Field(min_length=1, max_length=200)]
    ) -> dict[str, object]:
        """Get latest completed daily bar and any newer provisional intraday daily bar."""
        return service.get_latest_quote(symbol)

    @server.tool(title="List instrument members", annotations=READ_ONLY)
    def list_instrument_members(
        symbol: Annotated[str, Field(min_length=1, max_length=200)],
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        offset: Annotated[int, Field(ge=0, le=100_000)] = 0,
    ) -> dict[str, object]:
        """List board constituents, ETF PCF holdings, or custom-group members with provenance."""
        return service.list_instrument_members(symbol, limit, offset)

    @server.tool(title="List reverse board memberships", annotations=READ_ONLY)
    def list_symbol_boards(
        symbol: Annotated[str, Field(min_length=1, max_length=200)],
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        offset: Annotated[int, Field(ge=0, le=100_000)] = 0,
    ) -> dict[str, object]:
        """List active concept and industry boards containing a stock symbol."""
        return service.list_symbol_boards(symbol, limit, offset)

    return server


mcp = build_server()


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("STOCK_HARNESS_MCP_LOG_LEVEL", "WARNING"),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    mcp.run()


if __name__ == "__main__":
    main()
