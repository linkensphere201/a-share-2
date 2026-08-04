"""Local chart-serving HTTP API."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
import logging
from pathlib import Path
import time
from typing import Callable, Literal
from uuid import uuid4
import sqlite3

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from stock_harness.config import load_runtime_settings
from stock_harness.intraday import IntradayQuoteService
from stock_harness.models import InstrumentKind
from stock_harness.runtime_logging import EVENT_BUFFER, record_frontend_event
from stock_harness.sqlite_store import SQLiteMarketDataStore


LOGGER = logging.getLogger(__name__)


class CustomGroupMemberInput(BaseModel):
    symbol: str
    tags: list[str] = Field(default_factory=list)
    note: str = ""


class CustomGroupInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    members: list[CustomGroupMemberInput] = Field(default_factory=list, max_length=5000)


class IntradaySubscriptionInput(BaseModel):
    group_id: str = Field(min_length=1, max_length=200)
    symbols: list[str] = Field(default_factory=list, max_length=5000)


class FrontendEventInput(BaseModel):
    level: Literal["WARNING", "ERROR"]
    logger: str = Field(default="app", max_length=100)
    message: str = Field(min_length=1, max_length=1000)


def create_app(
    store: SQLiteMarketDataStore | None = None,
    provider_config: Path = Path("config/providers.local.yaml"),
    storage_config: Path = Path("config/storage.local.yaml"),
    web_dist: Path | None = None,
    update_status: Callable[[], dict[str, object]] | None = None,
    intraday_service: IntradayQuoteService | None = None,
) -> FastAPI:
    owned_store = store is None

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

    @app.get("/api/update-status")
    def auto_update_status() -> dict[str, object]:
        if update_status is None:
            return {"state": "disabled"}
        return update_status()

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
        return intraday_service.status() if intraday_service else {"state": "disabled", "enabled": False}

    @app.post("/api/intraday/subscription")
    def intraday_subscription(request: Request, payload: IntradaySubscriptionInput) -> dict[str, object]:
        if intraday_service is None:
            return {"state": "disabled", "enabled": False}
        symbols = _expand_subscription_symbols(_store(request), payload.symbols)
        try:
            return intraday_service.subscribe(payload.group_id, symbols)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/intraday-bars")
    def intraday_bars(symbol: list[str] = Query(default=[])) -> dict[str, object]:
        if intraday_service is None:
            return {"items": [], "status": {"state": "disabled", "enabled": False}}
        normalized = [item.upper() for item in symbol]
        return {"items": intraday_service.list(normalized), "status": intraday_service.status()}

    @app.get("/api/instruments")
    def instruments(
        request: Request,
        query: str = "",
        kind: list[InstrumentKind] | None = Query(default=None),
        source_system: str | None = None,
        family: str | None = None,
        category: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, object]:
        rows = _store(request).search_instruments(
            query=query,
            kinds=set(kind) if kind else None,
            source_system=source_system,
            family=family,
            category=category,
            limit=limit,
            offset=offset,
        )
        if offset == 0 and not kind and not source_system and not family and not category:
            groups = _store(request).list_custom_groups(query)
            custom_rows = [
                {
                    "symbol": item["symbol"], "name": item["name"],
                    "kind": "custom-group", "exchange": "LOCAL", "active": True,
                    "category": "自定义分组", "rows": item["member_count"],
                    "first_trade_date": None, "last_trade_date": None,
                }
                for item in groups
            ]
            rows = (custom_rows + rows)[:limit]
        return {"items": rows, "limit": limit, "offset": offset}

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

    @app.get("/api/instruments/{symbol}")
    def instrument(request: Request, symbol: str) -> dict[str, object]:
        row = _store(request).get_instrument_summary(symbol.upper())
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
        normalized = [item.upper() for item in symbol]
        return {"items": _store(request).list_market_snapshots(normalized)}

    @app.get("/api/instruments/{symbol}/daily-bars")
    def daily_bars(
        request: Request,
        symbol: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, object]:
        normalized = symbol.upper()
        if _store(request).get_instrument_summary(normalized) is None:
            raise HTTPException(status_code=404, detail="instrument not found")
        rows = _store(request).get_daily_bars(normalized, start_date, end_date)
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
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")

    return app


def _store(request: Request) -> SQLiteMarketDataStore:
    return request.app.state.store


def _expand_subscription_symbols(
    store: SQLiteMarketDataStore, symbols: list[str]
) -> list[str]:
    expanded: set[str] = set()
    for raw_symbol in symbols:
        symbol = raw_symbol.strip().upper()
        if not symbol:
            continue
        if not symbol.startswith("CUSTOM:"):
            expanded.add(symbol)
            continue
        custom = store.get_custom_group(symbol.split(":", 1)[1].lower())
        if custom is not None:
            expanded.update(str(item["symbol"]).upper() for item in custom["members"])
    return sorted(expanded)


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
            "total_market_cap": snapshot["total_market_cap"] if snapshot else None,
            "snapshot_date": snapshot["trade_date"] if snapshot else None,
        })
    return {
        "symbol": symbol, "relation": relation,
        "as_of_date": as_of_date, "source": source,
        "total": total, "items": enriched,
    }


app = create_app()
