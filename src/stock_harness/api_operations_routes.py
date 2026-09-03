"""Workspace, update, diagnostics, and provisional-quote HTTP routes."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, status

from stock_harness.api_models import (
    FrontendEventInput,
    IntradayRefreshInput,
    IntradaySubscriptionInput,
)
from stock_harness.api_runtime import runtime
from stock_harness.api_support import (
    effective_intraday_items,
    expand_subscription_symbols,
    futures_final_update_due,
    futures_reference_symbols,
    store,
)
from stock_harness.runtime_logging import EVENT_BUFFER, record_frontend_event
from stock_harness.workspace_context import WorkspaceContextInput


LOGGER = logging.getLogger(__name__)


def create_operations_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/workspace-context", status_code=status.HTTP_202_ACCEPTED)
    def publish_workspace_context(
        payload: WorkspaceContextInput, request: Request
    ) -> dict[str, object]:
        return runtime(request).workspace_context.publish(payload)

    @router.get("/api/workspace-context")
    def active_workspace_context(request: Request) -> dict[str, object]:
        snapshot = runtime(request).workspace_context.get(store(request))
        if snapshot is None:
            raise HTTPException(
                status_code=404, detail="workspace context has not been published"
            )
        return snapshot

    @router.get("/api/update-status")
    def auto_update_status(request: Request) -> dict[str, object]:
        callback = runtime(request).update_status
        return {"state": "disabled"} if callback is None else callback()

    @router.post("/api/update/refresh", status_code=status.HTTP_202_ACCEPTED)
    def trigger_auto_update(request: Request) -> dict[str, object]:
        callback = runtime(request).update_trigger
        if callback is None:
            raise HTTPException(status_code=503, detail="auto update is disabled")
        result = callback()
        LOGGER.info(
            "manual_final_daily_update_requested accepted=%s state=%s",
            result.get("accepted"), result.get("state"),
        )
        return result

    @router.get("/api/runtime-events")
    def runtime_events(
        after_id: int = Query(default=0, ge=0),
        min_level: Literal["WARNING", "ERROR"] = "WARNING",
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, object]:
        return {"items": EVENT_BUFFER.list(after_id, min_level, limit)}

    @router.post("/api/runtime-events", status_code=status.HTTP_202_ACCEPTED)
    def frontend_event(payload: FrontendEventInput) -> dict[str, object]:
        event = record_frontend_event(payload.level, payload.message, payload.logger)
        return {"event_id": event.event_id}

    @router.get("/api/intraday/status")
    def intraday_status(request: Request) -> dict[str, object]:
        dependencies = runtime(request)
        result = (
            dependencies.intraday_service.status()
            if dependencies.intraday_service
            else {"state": "disabled", "enabled": False}
        )
        result["futures"] = (
            dependencies.futures_provisional_service.status()
            if dependencies.futures_provisional_service
            else {"state": "disabled", "enabled": False}
        )
        return result

    @router.post("/api/intraday/subscription")
    def intraday_subscription(
        request: Request, payload: IntradaySubscriptionInput
    ) -> dict[str, object]:
        dependencies = runtime(request)
        if (
            dependencies.intraday_service is None
            and dependencies.futures_provisional_service is None
        ):
            return {"state": "disabled", "enabled": False}
        selected_store = store(request)
        symbols = expand_subscription_symbols(selected_store, payload.symbols)
        futures_symbols = futures_reference_symbols(selected_store, symbols)
        futures_set = set(futures_symbols)
        stock_symbols = [symbol for symbol in symbols if symbol not in futures_set]
        try:
            result = (
                dependencies.intraday_service.subscribe(payload.group_id, stock_symbols)
                if dependencies.intraday_service
                else {"state": "disabled", "enabled": False}
            )
            result["futures"] = (
                dependencies.futures_provisional_service.subscribe(
                    payload.group_id, futures_symbols
                )
                if dependencies.futures_provisional_service
                else {"state": "disabled", "enabled": False}
            )
            return result
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/api/intraday-bars")
    def intraday_bars(
        request: Request, symbol: list[str] = Query(default=[])
    ) -> dict[str, object]:
        service = runtime(request).intraday_service
        if service is None:
            return {"items": [], "status": {"state": "disabled", "enabled": False}}
        normalized = [item.upper() for item in symbol]
        items, canonical_symbols = effective_intraday_items(
            store(request), service, normalized
        )
        return {
            "items": items,
            "canonical_symbols": canonical_symbols,
            "status": service.status(),
        }

    @router.post("/api/intraday/refresh")
    def intraday_refresh(
        request: Request, payload: IntradayRefreshInput
    ) -> dict[str, object]:
        dependencies = runtime(request)
        selected_store = store(request)
        futures_symbols = futures_reference_symbols(selected_store, payload.symbols)
        stock_symbols = [item for item in payload.symbols if item not in futures_symbols]
        items: list[dict[str, object]] = []
        canonical_symbols: list[str] = []
        stock_status: dict[str, object] = {"state": "disabled", "enabled": False}
        try:
            if dependencies.intraday_service is not None and stock_symbols:
                dependencies.intraday_service.refresh_symbols(stock_symbols)
                items, canonical_symbols = effective_intraday_items(
                    selected_store, dependencies.intraday_service, stock_symbols
                )
            if dependencies.intraday_service is not None:
                stock_status = dependencies.intraday_service.status()
            futures_status: dict[str, object] = (
                dependencies.futures_provisional_service.refresh_once(
                    manual=True, references=futures_symbols
                )
                if dependencies.futures_provisional_service is not None and futures_symbols
                else {"state": "disabled", "enabled": False}
            )
            if futures_symbols:
                futures_status["mode"] = "provisional"
            if (
                futures_symbols
                and futures_status.get("state") in {"disabled", "skipped"}
                and futures_status.get("skip_reason") in {None, "market-closed"}
                and dependencies.update_trigger is not None
                and futures_final_update_due(
                    selected_store,
                    futures_symbols,
                    dependencies.futures_final_cutoffs,
                    dependencies.now_provider(),
                )
            ):
                final_status = dependencies.update_trigger()
                futures_status = {
                    **futures_status,
                    "mode": "final",
                    "state": final_status.get("state", "queued"),
                    "final_update": final_status,
                }
            if futures_symbols:
                LOGGER.info("futures_manual_refresh_completed %s", {
                    "state": futures_status.get("state"),
                    "mode": futures_status.get("mode"),
                    "references": len(futures_symbols),
                    "received": futures_status.get(
                        "received_count", futures_status.get("received", 0)
                    ),
                    "stale": futures_status.get("stale_count", 0),
                    "skip_reason": futures_status.get("skip_reason"),
                })
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "items": items,
            "canonical_symbols": canonical_symbols,
            "status": stock_status,
            "futures": futures_status,
        }

    return router
