"""Local chart-serving HTTP API."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from stock_harness.config import load_runtime_settings
from stock_harness.models import InstrumentKind
from stock_harness.sqlite_store import SQLiteMarketDataStore


def create_app(
    store: SQLiteMarketDataStore | None = None,
    provider_config: Path = Path("config/providers.local.yaml"),
    storage_config: Path = Path("config/storage.local.yaml"),
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
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

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
        return {"items": rows, "limit": limit, "offset": offset}

    @app.get("/api/instruments/{symbol}")
    def instrument(request: Request, symbol: str) -> dict[str, object]:
        row = _store(request).get_instrument_summary(symbol.upper())
        if row is None:
            raise HTTPException(status_code=404, detail="instrument not found")
        return row

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
        return {
            "symbol": normalized,
            "items": [
                {
                    "trade_date": row.trade_date,
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "volume": row.volume,
                    "source": row.source,
                }
                for row in rows
            ],
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

    @app.get("/api/instruments/{symbol}/boards")
    def symbol_boards(
        request: Request,
        symbol: str,
        limit: int = Query(default=500, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, object]:
        rows = _store(request).list_symbol_boards(symbol.upper(), limit, offset)
        return {"symbol": symbol.upper(), "items": rows, "limit": limit, "offset": offset}

    return app


def _store(request: Request) -> SQLiteMarketDataStore:
    return request.app.state.store


app = create_app()
