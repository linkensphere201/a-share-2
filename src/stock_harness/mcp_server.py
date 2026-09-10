"""StockHarness read-only MCP server entry point."""

from __future__ import annotations

import logging
import os
import sys
from functools import partial
from typing import Annotated, Literal

import anyio
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from stock_harness.mcp_tools import LocalStockHarnessApi, StockHarnessMcpTools


READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
LOCAL_APPEND = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)
LOCAL_COMPUTE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
EMBEDDED_CHAT_PROFILE = "embedded-chat"
INSTRUCTIONS = (
    "StockHarness is a local A-share and domestic-futures research source. Market, workspace, "
    "and generated-analysis tools are read-only. save_ai_analysis is the only write tool and "
    "may only append a versioned AI interpretation linked to explicit evidence references. "
    "Always report exact symbol, effective trading date, source, units, and final/intraday "
    "state. Treat intraday bars as provisional and continuous-series findings as derived "
    "evidence with explicit price basis and mapping. Never infer trading authorization or "
    "attempt mutation."
)
EMBEDDED_INSTRUCTIONS = (
    "This server is the isolated StockHarness evidence plane for application-owned Codex Chat. "
    "All market, workspace, membership, and prior-analysis tools are bounded local reads. "
    "recalculate_trend_analysis is the only compute operation; it reads persisted bars and "
    "appends a generated detector run without refreshing Providers or mutating market data. "
    "No trading, filesystem, SQL, credential, Provider-control, or arbitrary network capability "
    "is exposed."
)


def build_server(
    tools: StockHarnessMcpTools | None = None,
    *,
    profile: str | None = None,
) -> MCPServer:
    if profile not in {None, EMBEDDED_CHAT_PROFILE}:
        raise ValueError(f"unsupported StockHarness MCP profile: {profile}")
    service = tools or StockHarnessMcpTools(
        LocalStockHarnessApi(
            os.environ.get("STOCK_HARNESS_API_URL", "http://127.0.0.1:8001"),
            float(os.environ.get("STOCK_HARNESS_MCP_TIMEOUT_SECONDS", "5")),
        )
    )
    server = MCPServer(
        "StockHarness",
        instructions=(
            EMBEDDED_INSTRUCTIONS if profile == EMBEDDED_CHAT_PROFILE else INSTRUCTIONS
        ),
    )

    async def invoke(callback, *args):
        return await anyio.to_thread.run_sync(
            partial(callback, *args), abandon_on_cancel=True
        )

    @server.tool(title="Check StockHarness availability", annotations=READ_ONLY)
    async def stock_harness_health() -> dict[str, object]:
        """Check whether the running local StockHarness APP API is available."""
        return await invoke(service.health)

    @server.tool(title="Get active StockHarness workspace", annotations=READ_ONLY)
    async def get_active_workspace() -> dict[str, object]:
        """Get the active window group, chart views, links, symbols, and trend-line anchors."""
        return await invoke(service.get_active_workspace)

    @server.tool(title="List signal definitions", annotations=READ_ONLY)
    async def list_signal_definitions() -> dict[str, object]:
        """List versioned deterministic signal modules and their manual cadence."""
        return await invoke(service.list_signal_definitions)

    @server.tool(title="List signal review runs", annotations=READ_ONLY)
    async def list_signal_runs(
        signal_id: Annotated[str | None, Field(max_length=100)] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> dict[str, object]:
        """List immutable signal revisions and aggregate comparison counts."""
        return await invoke(service.list_signal_runs, signal_id, limit)

    @server.tool(title="Get signal review run", annotations=READ_ONLY)
    async def get_signal_run(
        run_id: Annotated[str, Field(min_length=1, max_length=64)],
        max_items: Annotated[int, Field(ge=1, le=200)] = 200,
    ) -> dict[str, object]:
        """Read one immutable signal run with bounded items and evidence references."""
        return await invoke(service.get_signal_run, run_id, max_items)

    @server.tool(title="Get signal review item", annotations=READ_ONLY)
    async def get_signal_item(
        run_id: Annotated[str, Field(min_length=1, max_length=64)],
        item_id: Annotated[str, Field(min_length=1, max_length=100)],
    ) -> dict[str, object]:
        """Read one exact immutable signal item and all of its stored evidence references."""
        return await invoke(service.get_signal_item, run_id, item_id)

    @server.tool(title="List signal-system scores", annotations=READ_ONLY)
    async def list_signal_scores(
        run_id: Annotated[str, Field(min_length=1, max_length=64)],
        system_id: Annotated[str | None, Field(max_length=100)] = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 200,
    ) -> dict[str, object]:
        """Read independent trend, hotspot, or future plugin scores for one run."""
        return await invoke(service.list_signal_scores, run_id, system_id, limit)

    @server.tool(title="Search StockHarness instruments", annotations=READ_ONLY)
    async def search_instruments(
        query: Annotated[str, Field(max_length=100)] = "",
        classification: Literal[
            "stock", "etf", "index", "concept", "industry", "sector",
            "futures", "futures-product", "futures-contract", "futures-continuous",
        ] | None = None,
        source_system: Annotated[str | None, Field(max_length=40)] = None,
        family: Annotated[str | None, Field(max_length=80)] = None,
        category: Annotated[str | None, Field(max_length=80)] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        offset: Annotated[int, Field(ge=0, le=100_000)] = 0,
    ) -> dict[str, object]:
        """Search stocks, ETFs, indices, boards, and custom groups by name, symbol, or pinyin."""
        return await invoke(
            service.search_instruments,
            query, classification, source_system, family, category, limit, offset
        )

    @server.tool(title="Get instrument metadata", annotations=READ_ONLY)
    async def get_instrument(
        symbol: Annotated[str, Field(min_length=1, max_length=200)]
    ) -> dict[str, object]:
        """Get one canonical instrument's identity, catalog, coverage, and open incidents."""
        return await invoke(service.get_instrument, symbol)

    @server.tool(title="List custom groups", annotations=READ_ONLY)
    async def list_custom_groups(
        query: Annotated[str, Field(max_length=100)] = "",
        limit: Annotated[int, Field(ge=1, le=200)] = 100,
    ) -> dict[str, object]:
        """List user-defined StockHarness groups and their latest summary metrics."""
        return await invoke(service.list_custom_groups, query, limit)

    @server.tool(title="Get custom group", annotations=READ_ONLY)
    async def get_custom_group(
        group_id: Annotated[str, Field(min_length=1, max_length=100)],
        max_members: Annotated[int, Field(ge=1, le=500)] = 500,
    ) -> dict[str, object]:
        """Get an ordered custom group with member roles, tags, notes, and availability."""
        return await invoke(service.get_custom_group, group_id, max_members)

    @server.tool(title="Get daily OHLCV bars", annotations=READ_ONLY)
    async def get_daily_bars(
        symbol: Annotated[str, Field(min_length=1, max_length=200)],
        start_date: Annotated[str | None, Field(description="Inclusive YYYY-MM-DD date.")] = None,
        end_date: Annotated[str | None, Field(description="Inclusive YYYY-MM-DD date.")] = None,
        max_bars: Annotated[int, Field(ge=1, le=8000)] = 8000,
    ) -> dict[str, object]:
        """Read bounded daily OHLCV with canonical/provisional fusion and explicit bar state."""
        return await invoke(service.get_daily_bars, symbol, start_date, end_date, max_bars)

    @server.tool(title="Get latest quote state", annotations=READ_ONLY)
    async def get_latest_quote(
        symbol: Annotated[str, Field(min_length=1, max_length=200)]
    ) -> dict[str, object]:
        """Get latest completed daily bar and any newer provisional intraday daily bar."""
        return await invoke(service.get_latest_quote, symbol)

    @server.tool(title="List futures coverage", annotations=READ_ONLY)
    async def list_futures_coverage(
        kind: Literal["futures-contract", "futures-continuous"] | None = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        offset: Annotated[int, Field(ge=0, le=100_000)] = 0,
    ) -> dict[str, object]:
        """List bounded real/continuous futures coverage and field-completeness evidence."""
        return await invoke(service.list_futures_coverage, kind, limit, offset)

    @server.tool(title="Inspect futures continuous series", annotations=READ_ONLY)
    async def get_futures_continuous(
        symbol: Annotated[str, Field(min_length=1, max_length=200)],
        start_date: Annotated[str | None, Field(description="Inclusive YYYY-MM-DD date.")] = None,
        end_date: Annotated[str | None, Field(description="Inclusive YYYY-MM-DD date.")] = None,
        max_mappings: Annotated[int, Field(ge=1, le=500)] = 200,
        max_rolls: Annotated[int, Field(ge=1, le=500)] = 200,
    ) -> dict[str, object]:
        """Inspect price basis, effective contract mappings, roll events, and build provenance."""
        return await invoke(
            service.get_futures_continuous,
            symbol, start_date, end_date, max_mappings, max_rolls,
        )

    @server.tool(title="Get trend-analysis evidence", annotations=READ_ONLY)
    async def get_trend_analysis(
        symbol: Annotated[str, Field(min_length=1, max_length=200)],
        timeframe: Literal["daily", "weekly", "monthly"] = "daily",
    ) -> dict[str, object]:
        """Read official/preview trend evidence without triggering recalculation."""
        return await invoke(service.get_trend_analysis, symbol, timeframe)

    if profile == EMBEDDED_CHAT_PROFILE:
        @server.tool(
            title="Recalculate daily trend analysis",
            annotations=LOCAL_COMPUTE,
        )
        async def recalculate_trend_analysis(
            symbol: Annotated[str, Field(min_length=1, max_length=200)],
            short_horizon_bars: Annotated[int, Field(ge=5, le=250)] = 60,
            medium_horizon_bars: Annotated[int, Field(ge=10, le=500)] = 120,
            long_horizon_bars: Annotated[int, Field(ge=60, le=1000)] = 250,
            include_preview: bool = True,
        ) -> dict[str, object]:
            """Recompute one symbol from stored bars only, then return bounded run summaries."""
            return await invoke(
                service.recalculate_trend_analysis,
                symbol,
                short_horizon_bars,
                medium_horizon_bars,
                long_horizon_bars,
                include_preview,
            )

    if profile != EMBEDDED_CHAT_PROFILE:
        @server.tool(title="Save structured AI chart analysis", annotations=LOCAL_APPEND)
        async def save_ai_analysis(
            symbol: Annotated[str, Field(min_length=1, max_length=200)],
            as_of_date: Annotated[str, Field(description="Exact YYYY-MM-DD evidence cutoff.")],
            title: Annotated[str, Field(min_length=1, max_length=160)],
            conclusion_markdown: Annotated[str, Field(min_length=1, max_length=20_000)],
            key_level_codes: Annotated[list[str], Field(min_length=1, max_length=50)],
            structures: Annotated[list[dict[str, object]], Field(min_length=2, max_length=2)],
            risk_reward: Annotated[list[dict[str, object]], Field(min_length=1, max_length=20)],
            references: Annotated[list[dict[str, object]], Field(min_length=1, max_length=100)],
            source_run_id: Annotated[str | None, Field(max_length=64)] = None,
            timeframe: Literal["daily", "weekly", "monthly"] = "daily",
            author: Annotated[str, Field(min_length=1, max_length=80)] = "codex",
        ) -> dict[str, object]:
            """Append an AI analysis with key levels, two horizons, and risk/reward.

            Every reference needs a unique code cited in conclusion_markdown. References may
            bind analysis_item_id from source_run_id or define validated level/line geometry.
            """
            payload: dict[str, object] = {
                "symbol": symbol,
                "timeframe": timeframe,
                "as_of_date": as_of_date,
                "source_run_id": source_run_id,
                "title": title,
                "conclusion_markdown": conclusion_markdown,
                "framework": {
                    "key_level_codes": key_level_codes,
                    "structures": structures,
                    "risk_reward": risk_reward,
                },
                "references": references,
                "author": author,
            }
            return await invoke(service.save_ai_analysis, payload)

    @server.tool(title="Get latest AI chart analysis", annotations=READ_ONLY)
    async def get_ai_analysis(
        symbol: Annotated[str, Field(min_length=1, max_length=200)],
        timeframe: Literal["daily", "weekly", "monthly"] = "daily",
    ) -> dict[str, object]:
        """Read the latest versioned AI interpretation and its frozen evidence references."""
        return await invoke(service.get_ai_analysis, symbol, timeframe)

    @server.tool(title="List instrument members", annotations=READ_ONLY)
    async def list_instrument_members(
        symbol: Annotated[str, Field(min_length=1, max_length=200)],
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        offset: Annotated[int, Field(ge=0, le=100_000)] = 0,
    ) -> dict[str, object]:
        """List board constituents, ETF PCF holdings, or custom-group members with provenance."""
        return await invoke(service.list_instrument_members, symbol, limit, offset)

    @server.tool(title="List reverse board memberships", annotations=READ_ONLY)
    async def list_symbol_boards(
        symbol: Annotated[str, Field(min_length=1, max_length=200)],
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        offset: Annotated[int, Field(ge=0, le=100_000)] = 0,
    ) -> dict[str, object]:
        """List active concept and industry boards containing a stock symbol."""
        return await invoke(service.list_symbol_boards, symbol, limit, offset)

    return server


mcp = build_server()


def main(profile: str | None = None) -> None:
    logging.basicConfig(
        level=os.environ.get("STOCK_HARNESS_MCP_LOG_LEVEL", "WARNING"),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    selected_profile = profile or os.environ.get("STOCK_HARNESS_MCP_PROFILE")
    (mcp if selected_profile is None else build_server(profile=selected_profile)).run()


if __name__ == "__main__":
    main()
