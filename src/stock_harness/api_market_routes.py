"""Instrument catalog, market data, custom group, and custom index routes."""

from __future__ import annotations

from datetime import date
import logging
import sqlite3
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from stock_harness.api_models import CustomGroupInput, CustomIndexInput, InstrumentTagsInput
from stock_harness.api_runtime import runtime
from stock_harness.api_support import (
    enrich_members,
    materialize_custom_index,
    normalize_instrument_symbol,
    store,
)
from stock_harness.models import FuturesBarState, InstrumentKind


LOGGER = logging.getLogger(__name__)


def create_market_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/instruments")
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
        selected_store = store(request)
        market_rows = selected_store.search_instruments(
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
        custom_rows: list[dict[str, object]] = []
        if (
            offset == 0 and not kind and not classification and not source_system
            and not family and not category
        ):
            custom_rows = [{
                "symbol": item["symbol"], "name": item["name"],
                "kind": "custom-group", "exchange": "LOCAL", "active": True,
                "category": "自定义分组", "rows": item["member_count"],
                "member_count": item["member_count"],
                "average_change_percent": item["average_change_percent"],
                "classification": "custom-group", "classification_label": "自选集合",
                "source_label": "本地", "first_trade_date": None,
                "last_trade_date": None,
            } for item in selected_store.list_custom_groups(query)][:max(0, limit - 1)]
        market_limit = limit - len(custom_rows)
        rows = market_rows[:market_limit]
        return {
            "items": custom_rows + rows, "limit": limit, "offset": offset,
            "has_more": len(market_rows) > market_limit,
            "next_offset": offset + len(rows),
        }

    @router.get("/api/futures/search-facets")
    def futures_search_facets(request: Request) -> dict[str, object]:
        selected_store = store(request)
        if not selected_store.futures_storage_status()["ready"]:
            return {"exchanges": [], "products": []}
        products = selected_store.list_futures_products()
        return {
            "exchanges": sorted({item.exchange.value for item in products}),
            "products": [{
                "symbol": item.symbol, "code": item.product_code,
                "name": item.display_name, "exchange": item.exchange.value,
                "active": item.active,
            } for item in products],
        }

    @router.get("/api/futures/coverage")
    def futures_coverage(
        request: Request,
        kind: Literal["futures-contract", "futures-continuous"] | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, object]:
        selected_kind = InstrumentKind(kind) if kind is not None else None
        rows = store(request).list_futures_coverage(
            kind=selected_kind, limit=limit + 1, offset=offset
        )
        return {
            "items": rows[:limit], "limit": limit, "offset": offset,
            "has_more": len(rows) > limit,
            "next_offset": offset + min(limit, len(rows)),
        }

    @router.get("/api/futures/continuous/{symbol}")
    def futures_continuous_detail(
        request: Request,
        symbol: str,
        start_date: date | None = None,
        end_date: date | None = None,
        max_mappings: int = Query(default=200, ge=1, le=500),
        max_rolls: int = Query(default=200, ge=1, le=500),
    ) -> dict[str, object]:
        selected_store = store(request)
        instrument = selected_store.get_instrument_summary(symbol)
        if instrument is None:
            raise HTTPException(status_code=404, detail="instrument not found")
        if instrument["kind"] != InstrumentKind.FUTURES_CONTINUOUS.value:
            raise HTTPException(
                status_code=400, detail="instrument is not a futures continuous series"
            )
        canonical = str(instrument["symbol"])
        start = start_date or date(1900, 1, 1)
        end = end_date or date.today()
        if start > end:
            raise HTTPException(status_code=422, detail="start_date must not exceed end_date")
        mappings = selected_store.list_futures_roll_mappings(canonical, start, end)
        rolls = selected_store.list_futures_continuous_roll_events(canonical)
        build = selected_store.get_futures_continuous_build_status(canonical)
        dirty = selected_store.get_futures_continuous_dirty_state(canonical)
        if build is not None and isinstance(build.get("input_digest"), bytes):
            build = {**build, "input_digest": build["input_digest"].hex()}
        roll_items = []
        for item in rolls[-max_rolls:]:
            payload = dict(item)
            if isinstance(payload.get("input_digest"), bytes):
                payload["input_digest"] = payload["input_digest"].hex()
            roll_items.append(payload)
        return {
            "instrument": instrument,
            "requested_range": {"start_date": start, "end_date": end},
            "mappings": [{
                "effective_from": item.effective_from,
                "contract_symbol": item.contract_symbol,
                "series_provider_symbol": item.series_provider_symbol,
                "contract_provider_symbol": item.contract_provider_symbol,
                "source": "tushare-futures",
            } for item in mappings[-max_mappings:]],
            "mapping_total": len(mappings),
            "mappings_truncated": len(mappings) > max_mappings,
            "build": build, "dirty": dirty, "rolls": roll_items,
            "roll_total": len(rolls), "rolls_truncated": len(rolls) > max_rolls,
        }

    @router.get("/api/custom-groups")
    def custom_groups(request: Request, query: str = "") -> dict[str, object]:
        return {"items": store(request).list_custom_groups(query)}

    @router.get("/api/custom-groups/{group_id}")
    def custom_group(request: Request, group_id: str) -> dict[str, object]:
        result = store(request).get_custom_group(group_id)
        if result is None:
            raise HTTPException(status_code=404, detail="custom group not found")
        return result

    @router.post("/api/custom-groups", status_code=status.HTTP_201_CREATED)
    def create_custom_group(
        request: Request, payload: CustomGroupInput
    ) -> dict[str, object]:
        try:
            result = store(request).create_custom_group(
                str(uuid4()), payload.name, payload.description,
                [item.model_dump() for item in payload.members],
            )
            LOGGER.info(
                "custom_group_created group_id=%s members=%s",
                result["id"], len(payload.members),
            )
            return result
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="custom group name already exists") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.put("/api/custom-groups/{group_id}")
    def update_custom_group(
        request: Request, group_id: str, payload: CustomGroupInput,
    ) -> dict[str, object]:
        try:
            result = store(request).update_custom_group(
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

    @router.delete("/api/custom-groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_custom_group(request: Request, group_id: str) -> Response:
        if not store(request).delete_custom_group(group_id):
            raise HTTPException(status_code=404, detail="custom group not found")
        LOGGER.info("custom_group_deleted group_id=%s", group_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/api/custom-indices")
    def custom_indices(request: Request, query: str = "") -> dict[str, object]:
        return {"items": store(request).list_custom_indices(query)}

    @router.get("/api/custom-indices/{index_id}")
    def custom_index(request: Request, index_id: str) -> dict[str, object]:
        result = store(request).get_custom_index(index_id)
        if result is None:
            raise HTTPException(status_code=404, detail="custom index not found")
        return result

    @router.post("/api/custom-indices", status_code=status.HTTP_201_CREATED)
    def create_custom_index(
        request: Request, payload: CustomIndexInput
    ) -> dict[str, object]:
        selected_store = store(request)
        dependencies = runtime(request)
        try:
            created = selected_store.create_custom_index(
                str(uuid4()), payload.name, payload.description, payload.base_date,
                payload.base_value, payload.weighting_method,
                [item.model_dump() for item in payload.members],
            )
            try:
                result = materialize_custom_index(
                    selected_store, str(created["id"]),
                    dependencies.custom_index_factor_loader,
                    dependencies.custom_index_status_loader,
                )
            except (RuntimeError, ValueError) as exc:
                LOGGER.warning(
                    "custom_index_initial_materialization_failed index_id=%s error=%s",
                    created["id"], exc,
                )
                result = selected_store.get_custom_index(str(created["id"])) or created
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

    @router.post("/api/custom-indices/{index_id}/rebuild")
    def rebuild_custom_index(request: Request, index_id: str) -> dict[str, object]:
        dependencies = runtime(request)
        try:
            result = materialize_custom_index(
                store(request), index_id,
                dependencies.custom_index_factor_loader,
                dependencies.custom_index_status_loader,
            )
            LOGGER.info("custom_index_rebuilt index_id=%s rows=%s", index_id, result["rows"])
            return result
        except (RuntimeError, ValueError) as exc:
            LOGGER.warning("custom_index_rebuild_failed index_id=%s error=%s", index_id, exc)
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.put("/api/custom-indices/{index_id}")
    def update_custom_index(
        request: Request, index_id: str, payload: CustomIndexInput
    ) -> dict[str, object]:
        selected_store = store(request)
        dependencies = runtime(request)
        try:
            updated = selected_store.update_custom_index(
                index_id, payload.name, payload.description, payload.base_date,
                payload.base_value, payload.weighting_method,
                [item.model_dump() for item in payload.members],
                payload.effective_from or date.today(),
            )
            if updated is None:
                raise HTTPException(status_code=404, detail="custom index not found")
            result = materialize_custom_index(
                selected_store, str(updated["id"]),
                dependencies.custom_index_factor_loader,
                dependencies.custom_index_status_loader,
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

    @router.delete("/api/custom-indices/{index_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_custom_index(request: Request, index_id: str) -> Response:
        if not store(request).delete_custom_index(index_id):
            raise HTTPException(status_code=404, detail="custom index not found")
        LOGGER.info("custom_index_deleted index_id=%s", index_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/api/instruments/{symbol}")
    def instrument(request: Request, symbol: str) -> dict[str, object]:
        row = store(request).get_instrument_summary(normalize_instrument_symbol(symbol))
        if row is None:
            raise HTTPException(status_code=404, detail="instrument not found")
        return row

    @router.get("/api/market-snapshots")
    def market_snapshots(
        request: Request, symbol: list[str] = Query(default=[])
    ) -> dict[str, object]:
        if len(symbol) > 500:
            raise HTTPException(status_code=422, detail="at most 500 symbols are allowed")
        return {"items": store(request).list_market_snapshots(symbol)}

    @router.get("/api/instrument-tags")
    def instrument_tags(
        request: Request, symbol: list[str] = Query(default=[])
    ) -> dict[str, object]:
        if len(symbol) > 500:
            raise HTTPException(status_code=422, detail="at most 500 symbols are allowed")
        normalized_symbols = list(dict.fromkeys(
            value.strip().upper() for value in symbol if value.strip()
        ))
        tags = store(request).list_instrument_tags(normalized_symbols)
        return {
            "items": [
                {"symbol": item, "tags": tags.get(item, [])}
                for item in normalized_symbols
            ]
        }

    @router.put("/api/instruments/{symbol}/tags")
    def update_instrument_tags(
        request: Request, symbol: str, payload: InstrumentTagsInput
    ) -> dict[str, object]:
        normalized = normalize_instrument_symbol(symbol)
        try:
            tags = store(request).replace_instrument_tags(normalized, payload.tags)
        except ValueError as exc:
            detail = str(exc)
            status_code = 404 if detail == "instrument not found" else 422
            raise HTTPException(status_code=status_code, detail=detail) from exc
        LOGGER.info("instrument_tags_updated symbol=%s count=%s", normalized, len(tags))
        return {"symbol": normalized, "tags": tags}

    @router.get("/api/instrument-board-tags")
    def instrument_board_tags(
        request: Request, symbol: list[str] = Query(default=[])
    ) -> dict[str, object]:
        if len(symbol) > 500:
            raise HTTPException(status_code=422, detail="at most 500 symbols are allowed")
        normalized_symbols = list(dict.fromkeys(
            value.strip().upper() for value in symbol if value.strip()
        ))
        tags = store(request).list_instrument_board_tags(normalized_symbols)
        return {"items": [
            {"symbol": item, "tags": tags.get(item, [])}
            for item in normalized_symbols
        ]}

    @router.post("/api/instrument-board-tags/rebuild")
    def rebuild_instrument_board_tags(request: Request) -> dict[str, object]:
        result = store(request).rebuild_instrument_board_tags()
        LOGGER.info(
            "instrument_board_tags_rebuilt stocks=%s tags=%s version=%s",
            result["tagged_stock_count"], result["tag_count"],
            result["algorithm_version"],
        )
        return result

    @router.get("/api/active-market-value")
    def active_market_value(request: Request) -> dict[str, object]:
        return store(request).get_active_market_value_index()

    @router.get("/api/active-market-value/daily")
    def active_market_value_daily(
        request: Request,
        start_date: date = date(1990, 1, 1),
        end_date: date = date.today(),
    ) -> dict[str, object]:
        if start_date > end_date:
            raise HTTPException(status_code=422, detail="start_date must not exceed end_date")
        return {"items": store(request).list_active_market_value_diagnostics(start_date, end_date)}

    @router.get("/api/active-market-value/diagnostics/latest")
    def active_market_value_latest_diagnostics(request: Request) -> dict[str, object]:
        return store(request).get_active_market_value_latest_diagnostics()

    @router.post("/api/active-market-value/rebuild")
    def rebuild_active_market_value(
        request: Request,
        mode: Literal["backfill", "incremental", "correction"] = "backfill",
        start_date: date | None = None,
    ) -> dict[str, object]:
        result = store(request).build_active_market_value_index(
            start_date=start_date, mode=mode
        )
        LOGGER.info(
            "active_market_value_rebuilt mode=%s rows=%s through=%s version=%s",
            mode, result["rows"], result.get("last_trade_date"),
            result.get("algorithm_version"),
        )
        return result

    @router.get("/api/instruments/{symbol}/daily-bars")
    def daily_bars(
        request: Request,
        symbol: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, object]:
        normalized = normalize_instrument_symbol(symbol)
        selected_store = store(request)
        instrument = selected_store.get_instrument_summary(normalized)
        if instrument is None:
            raise HTTPException(status_code=404, detail="instrument not found")
        if instrument["kind"] in {
            InstrumentKind.FUTURES_CONTRACT.value,
            InstrumentKind.FUTURES_CONTINUOUS.value,
        }:
            futures_rows = selected_store.list_fused_futures_daily_bars(
                normalized, start_date or date(1900, 1, 1), end_date or date.today()
            )
            return {
                "symbol": normalized,
                "instrument_kind": instrument["kind"],
                "price_basis": instrument.get("price_basis"),
                "rule_version": instrument.get("rule_version"),
                "items": [{
                    "trade_date": row.trading_day,
                    "open": row.open, "high": row.high, "low": row.low,
                    "close": row.close, "volume": row.volume_contracts,
                    "amount": row.amount, "previous_close": row.previous_close,
                    "settlement": row.settlement,
                    "previous_settlement": row.previous_settlement,
                    "open_interest": row.open_interest_contracts,
                    "open_interest_change": row.open_interest_change_contracts,
                    "mapped_contract_symbol": row.mapped_contract_symbol,
                    "roll_event": row.roll_event, "source": row.source,
                    "bar_state": "intraday" if row.state is FuturesBarState.PROVISIONAL else "final",
                    "stale": row.stale,
                    "provider_time": row.provider_time.isoformat() if row.provider_time else None,
                } for row in futures_rows],
            }
        rows = selected_store.get_daily_bars(normalized, start_date, end_date)
        items = [{
            "trade_date": row.trade_date, "open": row.open, "high": row.high,
            "low": row.low, "close": row.close, "volume": row.volume,
            "source": row.source, "bar_state": "final",
        } for row in rows]
        service = runtime(request).intraday_service
        provisional = service.get(normalized) if service else None
        if provisional is not None:
            provisional_date = provisional["trade_date"]
            last_final_date = rows[-1].trade_date if rows else None
            in_requested_range = (
                (start_date is None or provisional_date >= start_date)
                and (end_date is None or provisional_date <= end_date)
            )
            if in_requested_range and (
                last_final_date is None or provisional_date > last_final_date
            ):
                items.append({
                    "trade_date": provisional_date, "open": provisional["open"],
                    "high": provisional["high"], "low": provisional["low"],
                    "close": provisional["close"], "volume": provisional["volume"],
                    "source": provisional["source"], "bar_state": "intraday",
                    "stale": provisional["stale"],
                    "provider_time": provisional["provider_time"],
                })
        return {"symbol": normalized, "items": items}

    @router.get("/api/boards/{symbol}/members")
    def board_members(
        request: Request,
        symbol: str,
        limit: int = Query(default=500, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, object]:
        rows = store(request).list_board_members(symbol.upper(), limit, offset)
        return {"symbol": symbol.upper(), "items": rows, "limit": limit, "offset": offset}

    @router.get("/api/instruments/{symbol}/members")
    def instrument_members(
        request: Request,
        symbol: str,
        limit: int = Query(default=500, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, object]:
        normalized = symbol.upper()
        selected_store = store(request)
        if normalized.startswith("CUSTOM:"):
            custom = selected_store.get_custom_group(normalized.split(":", 1)[1].lower())
            if custom is None:
                raise HTTPException(status_code=404, detail="custom group not found")
            all_items = custom["members"]
            return enrich_members(
                selected_store, normalized, "custom_group_members", None,
                "local_custom_group", len(all_items), all_items[offset : offset + limit],
            )
        if normalized.startswith("CINDEX:"):
            custom_index = selected_store.get_custom_index(normalized)
            if custom_index is None:
                raise HTTPException(status_code=404, detail="custom index not found")
            all_items = custom_index["members"]
            return enrich_members(
                selected_store, normalized, "custom_index_members",
                custom_index["effective_from"], "local_custom_index",
                len(all_items), all_items[offset : offset + limit],
            )
        instrument = selected_store.get_instrument_summary(normalized)
        if instrument is None:
            raise HTTPException(status_code=404, detail="instrument not found")
        if instrument["kind"] == InstrumentKind.SECTOR.value:
            items = selected_store.list_board_members(normalized, limit, offset)
            as_of_date = max((item["last_seen_on"] for item in items), default=None)
            source = items[0]["source"] if items else None
            relation, total = "board_constituents", len(items)
        elif instrument["kind"] == InstrumentKind.ETF.value:
            result = selected_store.list_etf_holdings(normalized, limit, offset)
            if result is None:
                return {
                    "symbol": normalized, "relation": "etf_pcf", "as_of_date": None,
                    "source": None, "total": 0, "items": [],
                }
            items, as_of_date, source = result["items"], result["as_of_date"], result["source"]
            relation, total = "etf_pcf", result["total"]
        else:
            raise HTTPException(status_code=400, detail="instrument has no members")
        return enrich_members(
            selected_store, normalized, relation, as_of_date, source, total, items
        )

    @router.get("/api/instruments/{symbol}/boards")
    def symbol_boards(
        request: Request,
        symbol: str,
        limit: int = Query(default=500, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, object]:
        rows = store(request).list_symbol_boards(symbol.upper(), limit, offset)
        return {"symbol": symbol.upper(), "items": rows, "limit": limit, "offset": offset}

    return router
