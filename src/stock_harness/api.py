"""StockHarness FastAPI application composition root."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import date, datetime
import logging
from pathlib import Path
import os
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from stock_harness.api_analysis_routes import create_analysis_router
from stock_harness.api_chat_routes import create_chat_router
from stock_harness.api_market_routes import create_market_router
from stock_harness.api_models import (
    AiAnalysisFrameworkInput,
    AiAnalysisReferenceInput,
    AiAnalysisReportInput,
    AiChatConversationInput,
    AiChatConversationUpdateInput,
    AiChatTurnInput,
    AiPositionContextInput,
    AiRiskRewardContextInput,
    AiRiskRewardInput,
    AiStructureViewInput,
    CustomGroupInput,
    CustomGroupMemberInput,
    CustomGroupRole,
    InstrumentTagsInput,
    CustomIndexInput,
    CustomIndexMemberInput,
    FrontendEventInput,
    IntradayRefreshInput,
    IntradaySubscriptionInput,
    ScreenerRunInput,
    TrendAnalysisInput,
    TrendReviewCreateInput,
    TrendReviewLabelInput,
    TrendReviewSourceInput,
    TrendReviewUpdateInput,
)
from stock_harness.api_operations_routes import create_operations_router
from stock_harness.api_runtime import ApiRuntime
from stock_harness.api_support import CHINA_TIME
from stock_harness.chat_service import CodexChatService
from stock_harness.config import FuturesExchangeCutoff, load_runtime_settings
from stock_harness.codex_app_server import CodexAppServerClient, CodexBridge
from stock_harness.futures_intraday import FuturesProvisionalService
from stock_harness.intraday import IntradayQuoteService
from stock_harness.models import AdjustmentFactor, StockTradeStatus
from stock_harness.screener import ScreenerService
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.workspace_context import WorkspaceContextService


LOGGER = logging.getLogger(__name__)

__all__ = [
    "AiAnalysisFrameworkInput", "AiAnalysisReferenceInput", "AiAnalysisReportInput",
    "AiRiskRewardInput", "AiStructureViewInput", "AiChatConversationInput",
    "AiChatConversationUpdateInput", "AiChatTurnInput", "AiPositionContextInput",
    "AiRiskRewardContextInput", "CustomGroupInput",
    "CustomGroupMemberInput", "CustomGroupRole", "CustomIndexInput",
    "InstrumentTagsInput",
    "CustomIndexMemberInput", "FrontendEventInput", "IntradayRefreshInput",
    "IntradaySubscriptionInput", "ScreenerRunInput", "TrendAnalysisInput",
    "TrendReviewCreateInput", "TrendReviewLabelInput", "TrendReviewSourceInput",
    "TrendReviewUpdateInput", "app", "create_app",
]


def create_app(
    store: SQLiteMarketDataStore | None = None,
    provider_config: Path = Path("config/providers.local.yaml"),
    storage_config: Path = Path("config/storage.local.yaml"),
    web_dist: Path | None = None,
    update_status: Callable[[], dict[str, object]] | None = None,
    update_trigger: Callable[[], dict[str, object]] | None = None,
    intraday_service: IntradayQuoteService | None = None,
    futures_provisional_service: FuturesProvisionalService | None = None,
    futures_final_cutoffs: tuple[FuturesExchangeCutoff, ...] = (),
    now_provider: Callable[[], datetime] | None = None,
    custom_index_factor_loader: Callable[
        [list[str], date, date], list[AdjustmentFactor]
    ] | None = None,
    custom_index_status_loader: Callable[
        [list[str], date, date], list[StockTradeStatus]
    ] | None = None,
    codex_bridge: CodexBridge | None = None,
) -> FastAPI:
    """Create the API while preserving injectable services for tests and desktop."""
    owned_store = store is None
    workspace_context = WorkspaceContextService(intraday_service)
    resolved_now_provider = now_provider or (lambda: datetime.now(CHINA_TIME))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if store is None:
            settings = load_runtime_settings(provider_config, storage_config)
            app.state.store = SQLiteMarketDataStore(
                settings.database_path,
                cache_size_kib=settings.sqlite_cache_size_kib,
                mmap_size_mib=settings.sqlite_mmap_size_mib,
                temp_store=settings.sqlite_temp_store,
                busy_timeout_ms=settings.sqlite_busy_timeout_ms,
            )
        else:
            app.state.store = store
        app.state.screener = ScreenerService(app.state.store)
        bridge = codex_bridge or CodexAppServerClient(
            mcp_api_url=os.environ.get(
                "STOCK_HARNESS_EMBEDDED_MCP_API_URL", "http://127.0.0.1:8765"
            )
        )
        store_path = str(app.state.store.path)
        chat_workdir = (
            Path.cwd() / ".tmp" / "codex-chat"
            if store_path == ":memory:"
            else Path(store_path).resolve().parent / "runtime" / "codex-chat"
        )
        app.state.chat_service = CodexChatService(
            app.state.store, bridge, chat_workdir
        )
        yield
        app.state.chat_service.close()
        if owned_store:
            app.state.store.close()

    app = FastAPI(title="StockHarness API", version="0.1.0", lifespan=lifespan)
    app.state.api_runtime = ApiRuntime(
        workspace_context=workspace_context,
        update_status=update_status,
        update_trigger=update_trigger,
        intraday_service=intraday_service,
        futures_provisional_service=futures_provisional_service,
        futures_final_cutoffs=futures_final_cutoffs,
        now_provider=resolved_now_provider,
        custom_index_factor_loader=custom_index_factor_loader,
        custom_index_status_loader=custom_index_status_loader,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            LOGGER.exception(
                "api_unhandled_error method=%s path=%s",
                request.method,
                request.url.path,
            )
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        if elapsed_ms >= 2000:
            LOGGER.warning(
                "api_slow_request method=%s path=%s status=%s elapsed_ms=%.1f",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
            )
        return response

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(create_analysis_router())
    app.include_router(create_chat_router())
    app.include_router(create_operations_router())
    app.include_router(create_market_router())

    if web_dist is not None:
        index_file = web_dist / "index.html"
        if not index_file.is_file():
            raise ValueError(f"frontend build not found: {index_file}")

        @app.middleware("http")
        async def disable_frontend_entry_cache(request: Request, call_next):
            response = await call_next(request)
            if request.url.path in {"/", "/index.html"}:
                response.headers["Cache-Control"] = "no-store"
            return response

        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")

    return app


app = create_app()
