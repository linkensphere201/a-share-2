"""Local chart-serving HTTP API."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
import logging
from pathlib import Path
import time
from typing import Any, Callable, Literal
from uuid import uuid4
import sqlite3

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from stock_harness.config import FuturesExchangeCutoff, load_runtime_settings
from stock_harness.intraday import IntradayQuoteService
from stock_harness.futures_intraday import FuturesProvisionalService
from stock_harness.models import (
    AdjustmentFactor,
    FuturesBarState,
    FuturesExchange,
    InstrumentKind,
    StockTradeStatus,
)
from stock_harness.runtime_logging import EVENT_BUFFER, record_frontend_event
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.workspace_context import WorkspaceContextInput, WorkspaceContextService
from stock_harness.analysis_inputs import AnalysisHorizons, AnalysisTimeframe
from stock_harness.analysis_results import AnalysisNamespace
from stock_harness.trend_analysis import TrendAnalysisService
from stock_harness.trend_review_set import has_scoreable_expected_labels
from stock_harness.trend_reviews import (
    TrendReviewDecision,
    TrendReviewDraftSpec,
    TrendReviewLabel,
    TrendReviewStatus,
    labels_from_analysis,
)


LOGGER = logging.getLogger(__name__)
CHINA_TIME = timezone(timedelta(hours=8))

CustomGroupRole = Literal[
    "", "sentiment_anchor", "liquidity_anchor", "bellwether",
    "core_identity", "lagging_expansion",
]


class CustomGroupMemberInput(BaseModel):
    symbol: str
    role: CustomGroupRole = ""
    tags: list[str] = Field(default_factory=list)
    note: str = ""


class CustomGroupInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    members: list[CustomGroupMemberInput] = Field(default_factory=list, max_length=5000)


class CustomIndexMemberInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    weight: float | None = Field(default=None, gt=0)


class CustomIndexInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    base_date: date
    base_value: float = Field(default=1000, gt=0)
    weighting_method: Literal["equal", "manual"] = "equal"
    effective_from: date | None = None
    members: list[CustomIndexMemberInput] = Field(min_length=1, max_length=500)


class IntradaySubscriptionInput(BaseModel):
    group_id: str = Field(min_length=1, max_length=200)
    symbols: list[str] = Field(default_factory=list, max_length=5000)


class IntradayRefreshInput(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=5000)


class FrontendEventInput(BaseModel):
    level: Literal["WARNING", "ERROR"]
    logger: str = Field(default="app", max_length=100)
    message: str = Field(min_length=1, max_length=1000)


class TrendAnalysisInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    timeframes: list[Literal["daily", "weekly", "monthly"]] = Field(
        min_length=1, max_length=3
    )
    short_horizon_bars: int = Field(default=60, ge=1, le=1250)
    medium_horizon_bars: int = Field(default=120, ge=1, le=1250)
    long_horizon_bars: int = Field(default=250, ge=1, le=1250)
    config_version: str = Field(min_length=1, max_length=100)
    include_preview: bool = True


class TrendReviewSourceInput(BaseModel):
    provider: str = Field(min_length=1, max_length=100)
    dataset: str = Field(min_length=1, max_length=200)
    checked_on: date


class TrendReviewCreateInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    timeframe: Literal["daily"] = "daily"
    horizon: Literal["short", "long"] = "short"
    interval_start: date
    interval_end: date
    as_of_date: date
    dataset_version: str = Field(min_length=1, max_length=100)
    classification: Literal["positive", "near-miss", "ambiguous", "robustness"] = "ambiguous"
    tags: list[str] = Field(min_length=1, max_length=20)
    rationale: str = Field(default="", max_length=2000)
    sources: list[TrendReviewSourceInput] = Field(min_length=1, max_length=20)
    short_horizon_bars: int = Field(default=60, ge=20, le=120)
    medium_horizon_bars: int = Field(default=120, ge=60, le=500)
    long_horizon_bars: int = Field(default=250, ge=120, le=1250)
    config_version: str = Field(default="trend-review-ui-v1", min_length=1, max_length=100)


class TrendReviewLabelInput(BaseModel):
    item_id: str = Field(min_length=1, max_length=200)
    item_type: str = Field(min_length=1, max_length=50)
    decision: Literal["pending", "accepted", "rejected", "ambiguous"]
    payload: dict[str, Any]
    rationale: str = Field(default="", max_length=1000)


class TrendReviewUpdateInput(BaseModel):
    revision: int = Field(ge=1)
    review_status: Literal["proposed", "ambiguous", "confirmed", "rejected"]
    labels: list[TrendReviewLabelInput] = Field(max_length=5000)
    expected: dict[str, Any] = Field(default_factory=dict)
    rationale: str = Field(default="", max_length=2000)


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
) -> FastAPI:
    owned_store = store is None
    workspace_context = WorkspaceContextService(intraday_service)

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
        yield
        if owned_store:
            app.state.store.close()

    app = FastAPI(title="StockHarness API", version="0.1.0", lifespan=lifespan)
    now_provider = now_provider or (lambda: datetime.now(CHINA_TIME))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            LOGGER.exception("api_unhandled_error method=%s path=%s", request.method, request.url.path)
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        if elapsed_ms >= 2000:
            LOGGER.warning(
                "api_slow_request method=%s path=%s status=%s elapsed_ms=%.1f",
                request.method, request.url.path, response.status_code, elapsed_ms,
            )
        return response

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/analysis/trend/recalculate")
    def recalculate_trend_analysis(
        payload: TrendAnalysisInput, request: Request
    ) -> dict[str, object]:
        horizons = AnalysisHorizons(
            payload.short_horizon_bars,
            payload.medium_horizon_bars,
            payload.long_horizon_bars,
        )
        try:
            results = TrendAnalysisService(_store(request)).recalculate(
                payload.symbol,
                [AnalysisTimeframe(item) for item in payload.timeframes],
                horizons,
                config_version=payload.config_version,
                include_preview=payload.include_preview,
            )
        except ValueError as error:
            LOGGER.warning(
                "trend_analysis_rejected symbol=%s error=%s", payload.symbol, error
            )
            raise HTTPException(status_code=422, detail=str(error)) from error
        except RuntimeError as error:
            LOGGER.warning(
                "trend_analysis_busy symbol=%s error=%s", payload.symbol, error
            )
            raise HTTPException(status_code=409, detail=str(error)) from error
        LOGGER.info(
            "trend_analysis_completed symbol=%s timeframes=%s runs=%s",
            payload.symbol, payload.timeframes, len(results),
        )
        return {"status": "completed", "results": results}

    @app.get("/api/analysis/trend/{symbol}")
    def latest_trend_analysis(
        symbol: str,
        request: Request,
        timeframe: Literal["daily", "weekly", "monthly"] = "daily",
    ) -> dict[str, object]:
        selected_store = _store(request)
        official = selected_store.get_latest_generated_analysis_run(
            symbol, "trend", timeframe, AnalysisNamespace.OFFICIAL
        )
        preview = selected_store.get_latest_generated_analysis_run(
            symbol, "trend", timeframe, AnalysisNamespace.PREVIEW
        )
        now_ms = int(time.time() * 1000)
        preview_expired = bool(
            preview is not None
            and preview.get("expires_at_ms") is not None
            and int(preview["expires_at_ms"]) <= now_ms
        )
        effective = official
        if preview is not None and not preview_expired and (
            official is None or preview["as_of_date"] >= official["as_of_date"]
        ):
            effective = preview
        return {
            "symbol": symbol.upper(), "timeframe": timeframe,
            "official": _json_analysis_run(official),
            "preview": _json_analysis_run(preview),
            "preview_expired": preview_expired,
            "effective": _json_analysis_run(effective),
        }

    @app.post("/api/trend-reviews", status_code=status.HTTP_201_CREATED)
    def create_trend_review(
        payload: TrendReviewCreateInput,
        request: Request,
    ) -> dict[str, object]:
        horizons = AnalysisHorizons(
            payload.short_horizon_bars,
            payload.medium_horizon_bars,
            payload.long_horizon_bars,
        )
        try:
            snapshot = TrendAnalysisService(_store(request)).build_review_snapshot(
                payload.symbol,
                AnalysisTimeframe(payload.timeframe),
                horizons,
                as_of_date=payload.as_of_date,
                config_version=payload.config_version,
            )
            labels = labels_from_analysis(snapshot["items"], payload.horizon)
            review = _store(request).create_trend_review(TrendReviewDraftSpec(
                symbol=payload.symbol,
                timeframe=payload.timeframe,
                horizon=payload.horizon,
                interval_start=payload.interval_start,
                interval_end=payload.interval_end,
                as_of_date=payload.as_of_date,
                dataset_version=payload.dataset_version,
                classification=payload.classification,
                status=TrendReviewStatus.PROPOSED,
                tags=payload.tags,
                labels=labels,
                expected={},
                rationale=payload.rationale,
                sources=[item.model_dump(mode="json") for item in payload.sources],
                input_digest=bytes.fromhex(str(snapshot["input_digest"])),
                algorithm_version=str(snapshot["algorithm_version"]),
                config_version=str(snapshot["config_version"]),
                settings={
                    "short_horizon_bars": payload.short_horizon_bars,
                    "medium_horizon_bars": payload.medium_horizon_bars,
                    "long_horizon_bars": payload.long_horizon_bars,
                },
            ))
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        LOGGER.info(
            "trend_review_created review_id=%s symbol=%s as_of=%s labels=%s",
            review["review_id"], payload.symbol, payload.as_of_date, len(labels),
        )
        return {"review": _json_trend_review(review), "analysis": snapshot}

    @app.get("/api/trend-reviews")
    def list_trend_reviews(
        request: Request,
        symbol: str | None = None,
        review_status: Literal["proposed", "ambiguous", "confirmed", "rejected"] | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, object]:
        status_filter = TrendReviewStatus(review_status) if review_status else None
        items = _store(request).list_trend_reviews(
            symbol=symbol, status=status_filter, limit=limit
        )
        return {"items": [_json_trend_review(item) for item in items]}

    @app.get("/api/trend-reviews/{review_id}")
    def get_trend_review(review_id: str, request: Request) -> dict[str, object]:
        review = _store(request).get_trend_review(review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="trend review not found")
        return _json_trend_review(review)

    @app.post("/api/trend-reviews/{review_id}/snapshot")
    def rebuild_trend_review_snapshot(
        review_id: str,
        request: Request,
    ) -> dict[str, object]:
        review = _store(request).get_trend_review(review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="trend review not found")
        settings = review["settings"]
        if not isinstance(settings, dict):
            raise HTTPException(status_code=409, detail="trend review settings are invalid")
        try:
            snapshot = TrendAnalysisService(_store(request)).build_review_snapshot(
                str(review["symbol"]),
                AnalysisTimeframe(str(review["timeframe"])),
                AnalysisHorizons(
                    int(settings["short_horizon_bars"]),
                    int(settings["medium_horizon_bars"]),
                    int(settings["long_horizon_bars"]),
                ),
                as_of_date=review["as_of_date"],
                config_version=str(review["config_version"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if str(snapshot["input_digest"]) != bytes(review["input_digest"]).hex():
            raise HTTPException(
                status_code=409,
                detail="trend review input has changed since the draft was created",
            )
        return snapshot

    @app.put("/api/trend-reviews/{review_id}")
    def update_trend_review(
        review_id: str,
        payload: TrendReviewUpdateInput,
        request: Request,
    ) -> dict[str, object]:
        if (
            payload.review_status == TrendReviewStatus.CONFIRMED.value
            and not has_scoreable_expected_labels(payload.expected)
        ):
            raise HTTPException(
                status_code=422,
                detail="confirmed trend review requires scoreable expected labels",
            )
        if (
            payload.review_status == TrendReviewStatus.CONFIRMED.value
            and any(item.decision == TrendReviewDecision.PENDING.value for item in payload.labels)
        ):
            raise HTTPException(
                status_code=422,
                detail="confirmed trend review cannot contain pending labels",
            )
        labels = [TrendReviewLabel(
            item_id=item.item_id,
            item_type=item.item_type,
            decision=TrendReviewDecision(item.decision),
            payload=item.payload,
            rationale=item.rationale,
        ) for item in payload.labels]
        try:
            review = _store(request).update_trend_review(
                review_id,
                expected_revision=payload.revision,
                status=TrendReviewStatus(payload.review_status),
                labels=labels,
                expected=payload.expected,
                rationale=payload.rationale,
            )
        except ValueError as error:
            status_code = 404 if str(error) == "trend review not found" else 422
            raise HTTPException(status_code=status_code, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        LOGGER.info(
            "trend_review_updated review_id=%s status=%s revision=%s",
            review_id, payload.review_status, review["revision"],
        )
        return _json_trend_review(review)

    @app.post("/api/workspace-context", status_code=status.HTTP_202_ACCEPTED)
    def publish_workspace_context(payload: WorkspaceContextInput) -> dict[str, object]:
        return workspace_context.publish(payload)

    @app.get("/api/workspace-context")
    def active_workspace_context(request: Request) -> dict[str, object]:
        snapshot = workspace_context.get(_store(request))
        if snapshot is None:
            raise HTTPException(status_code=404, detail="workspace context has not been published")
        return snapshot

    @app.get("/api/update-status")
    def auto_update_status() -> dict[str, object]:
        if update_status is None:
            return {"state": "disabled"}
        return update_status()

    @app.post("/api/update/refresh", status_code=status.HTTP_202_ACCEPTED)
    def trigger_auto_update() -> dict[str, object]:
        if update_trigger is None:
            raise HTTPException(status_code=503, detail="auto update is disabled")
        result = update_trigger()
        LOGGER.info(
            "manual_final_daily_update_requested accepted=%s state=%s",
            result.get("accepted"), result.get("state"),
        )
        return result

    @app.get("/api/runtime-events")
    def runtime_events(
        after_id: int = Query(default=0, ge=0),
        min_level: Literal["WARNING", "ERROR"] = "WARNING",
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, object]:
        return {"items": EVENT_BUFFER.list(after_id, min_level, limit)}

    @app.post("/api/runtime-events", status_code=status.HTTP_202_ACCEPTED)
    def frontend_event(payload: FrontendEventInput) -> dict[str, object]:
        event = record_frontend_event(payload.level, payload.message, payload.logger)
        return {"event_id": event.event_id}

    @app.get("/api/intraday/status")
    def intraday_status() -> dict[str, object]:
        result = intraday_service.status() if intraday_service else {"state": "disabled", "enabled": False}
        result["futures"] = (
            futures_provisional_service.status()
            if futures_provisional_service else {"state": "disabled", "enabled": False}
        )
        return result

    @app.post("/api/intraday/subscription")
    def intraday_subscription(request: Request, payload: IntradaySubscriptionInput) -> dict[str, object]:
        if intraday_service is None and futures_provisional_service is None:
            return {"state": "disabled", "enabled": False}
        symbols = _expand_subscription_symbols(_store(request), payload.symbols)
        futures_symbols = _futures_reference_symbols(_store(request), symbols)
        futures_set = set(futures_symbols)
        stock_symbols = [symbol for symbol in symbols if symbol not in futures_set]
        try:
            result = (
                intraday_service.subscribe(payload.group_id, stock_symbols)
                if intraday_service else {"state": "disabled", "enabled": False}
            )
            result["futures"] = (
                futures_provisional_service.subscribe(
                    payload.group_id,
                    futures_symbols,
                )
                if futures_provisional_service
                else {"state": "disabled", "enabled": False}
            )
            return result
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/intraday-bars")
    def intraday_bars(
        request: Request, symbol: list[str] = Query(default=[])
    ) -> dict[str, object]:
        if intraday_service is None:
            return {"items": [], "status": {"state": "disabled", "enabled": False}}
        normalized = [item.upper() for item in symbol]
        items, canonical_symbols = _effective_intraday_items(
            _store(request), intraday_service, normalized
        )
        return {
            "items": items,
            "canonical_symbols": canonical_symbols,
            "status": intraday_service.status(),
        }

    @app.post("/api/intraday/refresh")
    def intraday_refresh(
        request: Request, payload: IntradayRefreshInput
    ) -> dict[str, object]:
        store = _store(request)
        futures_symbols = _futures_reference_symbols(store, payload.symbols)
        stock_symbols = [item for item in payload.symbols if item not in futures_symbols]
        items: list[dict[str, object]] = []
        canonical_symbols: list[str] = []
        stock_status: dict[str, object] = {"state": "disabled", "enabled": False}
        try:
            if intraday_service is not None and stock_symbols:
                intraday_service.refresh_symbols(stock_symbols)
                items, canonical_symbols = _effective_intraday_items(
                    store, intraday_service, stock_symbols
                )
            if intraday_service is not None:
                stock_status = intraday_service.status()
            futures_status: dict[str, object] = (
                futures_provisional_service.refresh_once(
                    manual=True, references=futures_symbols
                )
                if futures_provisional_service is not None and futures_symbols
                else {"state": "disabled", "enabled": False}
            )
            if futures_symbols:
                futures_status["mode"] = "provisional"
            if (
                futures_symbols
                and futures_status.get("state") in {"disabled", "skipped"}
                and futures_status.get("skip_reason") in {None, "market-closed"}
                and update_trigger is not None
                and _futures_final_update_due(
                    store, futures_symbols, futures_final_cutoffs, now_provider()
                )
            ):
                final_status = update_trigger()
                futures_status = {
                    **futures_status,
                    "mode": "final",
                    "state": final_status.get("state", "queued"),
                    "final_update": final_status,
                }
            if futures_symbols:
                log_payload = {
                    "state": futures_status.get("state"),
                    "mode": futures_status.get("mode"),
                    "references": len(futures_symbols),
                    "received": futures_status.get(
                        "received_count", futures_status.get("received", 0)
                    ),
                    "stale": futures_status.get("stale_count", 0),
                    "skip_reason": futures_status.get("skip_reason"),
                }
                LOGGER.info("futures_manual_refresh_completed %s", log_payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "items": items,
            "canonical_symbols": canonical_symbols,
            "status": stock_status,
            "futures": futures_status,
        }

    @app.get("/api/instruments")
    def instruments(
        request: Request,
        query: str = "",
        kind: list[InstrumentKind] | None = Query(default=None),
        classification: Literal[
            "stock", "etf", "index", "custom-index", "concept", "industry", "sector",
            "futures", "futures-product", "futures-contract", "futures-continuous",
        ] | None = None,
        source_system: str | None = None,
        family: str | None = None,
        category: str | None = None,
        exchange: str | None = None,
        futures_product: str | None = None,
        futures_lifecycle: Literal[
            "pending", "listed", "trading", "expired", "delivered", "delisted"
        ] | None = None,
        futures_series_kind: Literal["main", "continuous"] | None = None,
        active: bool | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, object]:
        rows = _store(request).search_instruments(
            query=query,
            kinds=set(kind) if kind else None,
            classification=classification,
            source_system=source_system,
            family=family,
            category=category,
            exchange=exchange,
            futures_product=futures_product,
            futures_lifecycle=futures_lifecycle,
            futures_series_kind=futures_series_kind,
            active=active,
            limit=limit + 1,
            offset=offset,
        )
        market_rows = rows
        custom_rows: list[dict[str, object]] = []
        if offset == 0 and not kind and not classification and not source_system and not family and not category:
            groups = _store(request).list_custom_groups(query)
            custom_rows = [
                {
                    "symbol": item["symbol"], "name": item["name"],
                    "kind": "custom-group", "exchange": "LOCAL", "active": True,
                    "category": "自定义分组", "rows": item["member_count"],
                    "member_count": item["member_count"],
                    "average_change_percent": item["average_change_percent"],
                    "classification": "custom-group", "classification_label": "自选集合",
                    "source_label": "本地",
                    "first_trade_date": None, "last_trade_date": None,
                }
                for item in groups
            ][:max(0, limit - 1)]
        market_limit = limit - len(custom_rows)
        rows = market_rows[:market_limit]
        has_more = len(market_rows) > market_limit
        return {
            "items": custom_rows + rows, "limit": limit, "offset": offset,
            "has_more": has_more, "next_offset": offset + len(rows),
        }

    @app.get("/api/futures/search-facets")
    def futures_search_facets(request: Request) -> dict[str, object]:
        store = _store(request)
        if not store.futures_storage_status()["ready"]:
            return {"exchanges": [], "products": []}
        products = store.list_futures_products()
        return {
            "exchanges": sorted({item.exchange.value for item in products}),
            "products": [
                {
                    "symbol": item.symbol,
                    "code": item.product_code,
                    "name": item.display_name,
                    "exchange": item.exchange.value,
                    "active": item.active,
                }
                for item in products
            ],
        }

    @app.get("/api/futures/coverage")
    def futures_coverage(
        request: Request,
        kind: Literal["futures-contract", "futures-continuous"] | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, object]:
        selected_kind = InstrumentKind(kind) if kind is not None else None
        rows = _store(request).list_futures_coverage(
            kind=selected_kind, limit=limit + 1, offset=offset
        )
        return {
            "items": rows[:limit],
            "limit": limit,
            "offset": offset,
            "has_more": len(rows) > limit,
            "next_offset": offset + min(limit, len(rows)),
        }

    @app.get("/api/custom-groups")
    def custom_groups(request: Request, query: str = "") -> dict[str, object]:
        return {"items": _store(request).list_custom_groups(query)}

    @app.get("/api/custom-groups/{group_id}")
    def custom_group(request: Request, group_id: str) -> dict[str, object]:
        result = _store(request).get_custom_group(group_id)
        if result is None:
            raise HTTPException(status_code=404, detail="custom group not found")
        return result

    @app.post("/api/custom-groups", status_code=status.HTTP_201_CREATED)
    def create_custom_group(request: Request, payload: CustomGroupInput) -> dict[str, object]:
        try:
            result = _store(request).create_custom_group(
                str(uuid4()), payload.name, payload.description,
                [item.model_dump() for item in payload.members],
            )
            LOGGER.info("custom_group_created group_id=%s members=%s", result["id"], len(payload.members))
            return result
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="custom group name already exists") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put("/api/custom-groups/{group_id}")
    def update_custom_group(
        request: Request, group_id: str, payload: CustomGroupInput,
    ) -> dict[str, object]:
        try:
            result = _store(request).update_custom_group(
                group_id, payload.name, payload.description,
                [item.model_dump() for item in payload.members],
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="custom group name already exists") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="custom group not found")
        LOGGER.info("custom_group_updated group_id=%s members=%s", group_id, len(payload.members))
        return result

    @app.delete("/api/custom-groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_custom_group(request: Request, group_id: str) -> Response:
        if not _store(request).delete_custom_group(group_id):
            raise HTTPException(status_code=404, detail="custom group not found")
        LOGGER.info("custom_group_deleted group_id=%s", group_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/custom-indices")
    def custom_indices(request: Request, query: str = "") -> dict[str, object]:
        return {"items": _store(request).list_custom_indices(query)}

    @app.get("/api/custom-indices/{index_id}")
    def custom_index(request: Request, index_id: str) -> dict[str, object]:
        result = _store(request).get_custom_index(index_id)
        if result is None:
            raise HTTPException(status_code=404, detail="custom index not found")
        return result

    @app.post("/api/custom-indices", status_code=status.HTTP_201_CREATED)
    def create_custom_index(request: Request, payload: CustomIndexInput) -> dict[str, object]:
        store = _store(request)
        try:
            created = store.create_custom_index(
                str(uuid4()), payload.name, payload.description, payload.base_date,
                payload.base_value, payload.weighting_method,
                [item.model_dump() for item in payload.members],
            )
            try:
                result = _materialize_custom_index(
                    store, str(created["id"]), custom_index_factor_loader,
                    custom_index_status_loader,
                )
            except (RuntimeError, ValueError) as exc:
                LOGGER.warning(
                    "custom_index_initial_materialization_failed index_id=%s error=%s",
                    created["id"], exc,
                )
                result = store.get_custom_index(str(created["id"])) or created
            LOGGER.info(
                "custom_index_created index_id=%s members=%s rows=%s",
                result["id"], len(payload.members), result["rows"],
            )
            return result
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="custom index name already exists") from exc
        except (RuntimeError, ValueError) as exc:
            LOGGER.warning("custom_index_create_failed error=%s", exc)
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/custom-indices/{index_id}/rebuild")
    def rebuild_custom_index(request: Request, index_id: str) -> dict[str, object]:
        try:
            result = _materialize_custom_index(
                _store(request), index_id, custom_index_factor_loader,
                custom_index_status_loader,
            )
            LOGGER.info("custom_index_rebuilt index_id=%s rows=%s", index_id, result["rows"])
            return result
        except (RuntimeError, ValueError) as exc:
            LOGGER.warning("custom_index_rebuild_failed index_id=%s error=%s", index_id, exc)
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put("/api/custom-indices/{index_id}")
    def update_custom_index(
        request: Request, index_id: str, payload: CustomIndexInput
    ) -> dict[str, object]:
        store = _store(request)
        try:
            updated = store.update_custom_index(
                index_id, payload.name, payload.description, payload.base_date,
                payload.base_value, payload.weighting_method,
                [item.model_dump() for item in payload.members],
                payload.effective_from or date.today(),
            )
            if updated is None:
                raise HTTPException(status_code=404, detail="custom index not found")
            result = _materialize_custom_index(
                store, str(updated["id"]), custom_index_factor_loader,
                custom_index_status_loader,
            )
            LOGGER.info(
                "custom_index_updated index_id=%s revision=%s rows=%s",
                index_id, result["revision_number"], result["rows"],
            )
            return result
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="custom index revision conflicts") from exc
        except (RuntimeError, ValueError) as exc:
            LOGGER.warning("custom_index_update_failed index_id=%s error=%s", index_id, exc)
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/api/custom-indices/{index_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_custom_index(request: Request, index_id: str) -> Response:
        if not _store(request).delete_custom_index(index_id):
            raise HTTPException(status_code=404, detail="custom index not found")
        LOGGER.info("custom_index_deleted index_id=%s", index_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/instruments/{symbol}")
    def instrument(request: Request, symbol: str) -> dict[str, object]:
        row = _store(request).get_instrument_summary(_normalize_instrument_symbol(symbol))
        if row is None:
            raise HTTPException(status_code=404, detail="instrument not found")
        return row

    @app.get("/api/market-snapshots")
    def market_snapshots(
        request: Request,
        symbol: list[str] = Query(default=[]),
    ) -> dict[str, object]:
        if len(symbol) > 500:
            raise HTTPException(status_code=422, detail="at most 500 symbols are allowed")
        return {"items": _store(request).list_market_snapshots(symbol)}

    @app.get("/api/instruments/{symbol}/daily-bars")
    def daily_bars(
        request: Request,
        symbol: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, object]:
        normalized = _normalize_instrument_symbol(symbol)
        store = _store(request)
        instrument = store.get_instrument_summary(normalized)
        if instrument is None:
            raise HTTPException(status_code=404, detail="instrument not found")
        if instrument["kind"] in {
            InstrumentKind.FUTURES_CONTRACT.value,
            InstrumentKind.FUTURES_CONTINUOUS.value,
        }:
            futures_rows = store.list_fused_futures_daily_bars(
                normalized,
                start_date or date(1900, 1, 1),
                end_date or date.today(),
            )
            return {
                "symbol": normalized,
                "instrument_kind": instrument["kind"],
                "price_basis": instrument.get("price_basis"),
                "rule_version": instrument.get("rule_version"),
                "items": [
                    {
                        "trade_date": row.trading_day,
                        "open": row.open, "high": row.high,
                        "low": row.low, "close": row.close,
                        "volume": row.volume_contracts,
                        "amount": row.amount,
                        "previous_close": row.previous_close,
                        "settlement": row.settlement,
                        "previous_settlement": row.previous_settlement,
                        "open_interest": row.open_interest_contracts,
                        "open_interest_change": row.open_interest_change_contracts,
                        "mapped_contract_symbol": row.mapped_contract_symbol,
                        "roll_event": row.roll_event,
                        "source": row.source,
                        "bar_state": (
                            "intraday"
                            if row.state is FuturesBarState.PROVISIONAL else "final"
                        ),
                        "provider_time": (
                            row.provider_time.isoformat() if row.provider_time else None
                        ),
                    }
                    for row in futures_rows
                ],
            }
        rows = store.get_daily_bars(normalized, start_date, end_date)
        items = [
            {
                "trade_date": row.trade_date,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
                "source": row.source,
                "bar_state": "final",
            }
            for row in rows
        ]
        provisional = intraday_service.get(normalized) if intraday_service else None
        if provisional is not None:
            provisional_date = provisional["trade_date"]
            last_final_date = rows[-1].trade_date if rows else None
            in_requested_range = (
                (start_date is None or provisional_date >= start_date)
                and (end_date is None or provisional_date <= end_date)
            )
            if in_requested_range and (last_final_date is None or provisional_date > last_final_date):
                items.append({
                    "trade_date": provisional_date,
                    "open": provisional["open"],
                    "high": provisional["high"],
                    "low": provisional["low"],
                    "close": provisional["close"],
                    "volume": provisional["volume"],
                    "source": provisional["source"],
                    "bar_state": "intraday",
                    "stale": provisional["stale"],
                    "provider_time": provisional["provider_time"],
                })
        return {
            "symbol": normalized,
            "items": items,
        }

    @app.get("/api/boards/{symbol}/members")
    def board_members(
        request: Request,
        symbol: str,
        limit: int = Query(default=500, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, object]:
        rows = _store(request).list_board_members(symbol.upper(), limit, offset)
        return {"symbol": symbol.upper(), "items": rows, "limit": limit, "offset": offset}

    @app.get("/api/instruments/{symbol}/members")
    def instrument_members(
        request: Request,
        symbol: str,
        limit: int = Query(default=500, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, object]:
        normalized = symbol.upper()
        store = _store(request)
        if normalized.startswith("CUSTOM:"):
            custom = store.get_custom_group(normalized.split(":", 1)[1].lower())
            if custom is None:
                raise HTTPException(status_code=404, detail="custom group not found")
            all_items = custom["members"]
            items = all_items[offset : offset + limit]
            relation = "custom_group_members"
            as_of_date = None
            source = "local_custom_group"
            total = len(all_items)
            return _enrich_members(store, normalized, relation, as_of_date, source, total, items)
        if normalized.startswith("CINDEX:"):
            custom_index = store.get_custom_index(normalized)
            if custom_index is None:
                raise HTTPException(status_code=404, detail="custom index not found")
            all_items = custom_index["members"]
            items = all_items[offset : offset + limit]
            return _enrich_members(
                store, normalized, "custom_index_members",
                custom_index["effective_from"], "local_custom_index",
                len(all_items), items,
            )
        instrument = store.get_instrument_summary(normalized)
        if instrument is None:
            raise HTTPException(status_code=404, detail="instrument not found")
        if instrument["kind"] == InstrumentKind.SECTOR.value:
            items = store.list_board_members(normalized, limit, offset)
            as_of_date = max(
                (item["last_seen_on"] for item in items), default=None
            )
            source = items[0]["source"] if items else None
            relation = "board_constituents"
            total = len(items)
        elif instrument["kind"] == InstrumentKind.ETF.value:
            result = store.list_etf_holdings(normalized, limit, offset)
            if result is None:
                return {
                    "symbol": normalized, "relation": "etf_pcf",
                    "as_of_date": None, "source": None, "total": 0, "items": [],
                }
            items = result["items"]
            as_of_date = result["as_of_date"]
            source = result["source"]
            relation = "etf_pcf"
            total = result["total"]
        else:
            raise HTTPException(status_code=400, detail="instrument has no members")
        return _enrich_members(store, normalized, relation, as_of_date, source, total, items)

    @app.get("/api/instruments/{symbol}/boards")
    def symbol_boards(
        request: Request,
        symbol: str,
        limit: int = Query(default=500, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, object]:
        rows = _store(request).list_symbol_boards(symbol.upper(), limit, offset)
        return {"symbol": symbol.upper(), "items": rows, "limit": limit, "offset": offset}

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


def _materialize_custom_index(
    store: SQLiteMarketDataStore,
    index_id: str,
    factor_loader: Callable[[list[str], date, date], list[AdjustmentFactor]] | None,
    status_loader: Callable[[list[str], date, date], list[StockTradeStatus]] | None,
) -> dict[str, object]:
    detail = store.get_custom_index(index_id)
    if detail is None:
        raise ValueError("custom index not found")
    symbols = [str(item["symbol"]) for item in detail["members"]]
    if factor_loader is not None:
        try:
            factors = factor_loader(symbols, detail["base_date"], date.today())
        except Exception as exc:
            store.mark_custom_index_error(index_id, f"adjustment factor load failed: {exc}")
            raise
        store.upsert_adjustment_factors("tushare", factors)
    if status_loader is not None:
        try:
            statuses = status_loader(symbols, detail["base_date"], date.today())
        except Exception as exc:
            store.mark_custom_index_error(index_id, f"trade status load failed: {exc}")
            raise
        store.upsert_stock_trade_statuses("tushare", statuses)
    return store.rebuild_custom_index(index_id)


def _store(request: Request) -> SQLiteMarketDataStore:
    return request.app.state.store


def _json_analysis_run(
    value: dict[str, object] | None,
) -> dict[str, object] | None:
    if value is None:
        return None
    result = dict(value)
    digest = result.get("input_digest")
    if isinstance(digest, bytes):
        result["input_digest"] = digest.hex()
    return result


def _json_trend_review(value: dict[str, object]) -> dict[str, object]:
    result = dict(value)
    digest = result.get("input_digest")
    if isinstance(digest, bytes):
        result["input_digest"] = digest.hex()
    return result


def _expand_subscription_symbols(
    store: SQLiteMarketDataStore, symbols: list[str]
) -> list[str]:
    expanded: set[str] = set()
    for raw_symbol in symbols:
        stripped = raw_symbol.strip()
        symbol = stripped if stripped.startswith("FUT") else stripped.upper()
        if not symbol:
            continue
        if not symbol.startswith("CUSTOM:"):
            expanded.add(symbol)
            continue
        custom = store.get_custom_group(symbol.split(":", 1)[1].lower())
        if custom is not None:
            expanded.update(
                value if value.startswith("FUT") else value.upper()
                for item in custom["members"]
                if (value := str(item["symbol"]).strip())
            )
    return sorted(expanded)


def _normalize_instrument_symbol(symbol: str) -> str:
    stripped = symbol.strip()
    return stripped if stripped.upper().startswith("FUT") else stripped.upper()


def _futures_reference_symbols(
    store: SQLiteMarketDataStore, symbols: list[str]
) -> list[str]:
    result: list[str] = []
    for symbol in symbols:
        try:
            kind = str(store.get_instrument_summary(symbol)["kind"])
        except (KeyError, TypeError):
            continue
        if kind in {
            InstrumentKind.FUTURES_CONTRACT.value,
            InstrumentKind.FUTURES_CONTINUOUS.value,
        }:
            result.append(symbol)
    return result


def _futures_final_update_due(
    store: SQLiteMarketDataStore,
    symbols: list[str],
    cutoffs: tuple[FuturesExchangeCutoff, ...],
    observed_at: datetime,
) -> bool:
    cutoff_by_exchange = {item.exchange: item.final_data_after for item in cutoffs}
    exchanges: set[FuturesExchange] = set()
    for symbol in symbols:
        summary = store.get_instrument_summary(symbol)
        if summary is None:
            return False
        try:
            exchanges.add(FuturesExchange(str(summary["exchange"])))
        except ValueError:
            return False
    if not exchanges or any(exchange not in cutoff_by_exchange for exchange in exchanges):
        return False
    local = (
        observed_at.replace(tzinfo=CHINA_TIME)
        if observed_at.tzinfo is None
        else observed_at.astimezone(CHINA_TIME)
    )
    current = local.time().replace(tzinfo=None)
    return all(current >= cutoff_by_exchange[exchange] for exchange in exchanges)


def _effective_intraday_items(
    store: SQLiteMarketDataStore,
    service: IntradayQuoteService,
    symbols: list[str],
) -> tuple[list[dict[str, object]], list[str]]:
    items = service.list(symbols)
    effective: list[dict[str, object]] = []
    canonical_symbols: list[str] = []
    for item in items:
        symbol = str(item["symbol"]).upper()
        final_date = store.get_latest_daily_bar_date(symbol)
        if final_date is None or item["trade_date"] > final_date:
            effective.append(item)
        else:
            canonical_symbols.append(symbol)
    return effective, canonical_symbols


def _enrich_members(
    store: SQLiteMarketDataStore,
    symbol: str,
    relation: str,
    as_of_date: object,
    source: object,
    total: int,
    items: list[dict[str, object]],
) -> dict[str, object]:
    member_symbols = [str(item["symbol"]) for item in items]
    snapshot_rows = []
    for chunk_start in range(0, len(member_symbols), 500):
        snapshot_rows.extend(store.list_market_snapshots(
            member_symbols[chunk_start : chunk_start + 500]
        ))
    snapshots = {item["symbol"]: item for item in snapshot_rows}
    enriched = []
    for item in items:
        snapshot = snapshots.get(str(item["symbol"]))
        enriched.append({
            **item,
            "change_percent": snapshot["change_percent"] if snapshot else None,
            "settlement_change_percent": (
                snapshot.get("settlement_change_percent") if snapshot else None
            ),
            "total_market_cap": snapshot["total_market_cap"] if snapshot else None,
            "close": snapshot["close"] if snapshot else None,
            "volume": snapshot["volume"] if snapshot else None,
            "amount": snapshot["amount"] if snapshot else None,
            "open_interest": snapshot.get("open_interest") if snapshot else None,
            "open_interest_change": (
                snapshot.get("open_interest_change") if snapshot else None
            ),
            "source_state": snapshot.get("source_state") if snapshot else None,
            "contract_month": snapshot.get("contract_month") if snapshot else None,
            "last_trading_date": (
                snapshot.get("last_trading_date") if snapshot else None
            ),
            "snapshot_date": snapshot["trade_date"] if snapshot else None,
        })
    return {
        "symbol": symbol, "relation": relation,
        "as_of_date": as_of_date, "source": source,
        "total": total, "items": enriched,
    }


app = create_app()
