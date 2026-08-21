"""Bounded in-memory publication of the active frontend workspace for MCP reads."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from threading import Lock
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from stock_harness.intraday import IntradayQuoteService
from stock_harness.sqlite_store import SQLiteMarketDataStore


WorkspaceSymbol = Annotated[str, Field(min_length=1, max_length=200)]


class WorkspaceInstrumentInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=40)
    exchange: str | None = Field(default=None, max_length=20)
    product_code: str | None = Field(default=None, max_length=40)
    lifecycle_status: str | None = Field(default=None, max_length=40)
    contract_month: str | None = Field(default=None, max_length=20)
    series_kind: str | None = Field(default=None, max_length=40)
    series_variant: str | None = Field(default=None, max_length=80)
    price_basis: str | None = Field(default=None, max_length=40)
    rule_version: str | None = Field(default=None, max_length=100)


class WorkspaceChartViewInput(BaseModel):
    range: Literal["1M", "1Y", "3Y", "10Y", "ALL"]
    coordinate_mode: Literal["normal", "log"]
    visible_start: str | None = Field(default=None, max_length=20)
    visible_end: str | None = Field(default=None, max_length=20)
    volume_visible: bool
    indicator: Literal["macd", "none"]


class WorkspaceWindowBaseInput(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    title: str = Field(max_length=200)
    mode: Literal["attached", "detached"]
    focused: bool
    maximized: bool


class WorkspaceChartWindowInput(WorkspaceWindowBaseInput):
    type: Literal["chart"]
    instrument: WorkspaceInstrumentInput
    chart: WorkspaceChartViewInput


class WorkspaceListWindowInput(WorkspaceWindowBaseInput):
    type: Literal["instrument-list"]
    instruments: list[WorkspaceInstrumentInput] = Field(default_factory=list, max_length=500)
    resolved_symbols: list[WorkspaceSymbol] = Field(default_factory=list, max_length=500)
    selected_symbol: str | None = Field(default=None, max_length=200)
    member_source_window_id: str | None = Field(default=None, max_length=200)


WorkspaceWindowInput = Annotated[
    WorkspaceChartWindowInput | WorkspaceListWindowInput,
    Field(discriminator="type"),
]


class WorkspaceAttachmentInput(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    type: Literal["show-symbol", "show-members"]
    source_window_id: str = Field(min_length=1, max_length=200)
    target_window_id: str = Field(min_length=1, max_length=200)


class WorkspaceTrendLineAnchorInput(BaseModel):
    date: str = Field(min_length=1, max_length=20)
    price: float
    snap: Literal["free", "high", "low"]


class WorkspaceTrendLineStyleInput(BaseModel):
    color: str = Field(min_length=1, max_length=40)
    width: Literal[1, 2, 3]
    dash: Literal["solid", "dotted", "dashed", "long-dashed", "dash-dot"]


class WorkspaceTrendLineInput(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    kind: Literal["trend-line"]
    symbol: str = Field(min_length=1, max_length=200)
    anchors: tuple[WorkspaceTrendLineAnchorInput, WorkspaceTrendLineAnchorInput]
    coordinate_mode: Literal["normal", "log"]
    style: WorkspaceTrendLineStyleInput
    visible: bool
    created_at: str = Field(max_length=40)
    updated_at: str = Field(max_length=40)


WorkspaceTrendLineList = Annotated[list[WorkspaceTrendLineInput], Field(max_length=200)]


class WorkspaceContextInput(BaseModel):
    schema_version: Literal["1.0"]
    published_at: datetime
    active_group_id: str = Field(min_length=1, max_length=200)
    active_group_name: str = Field(max_length=200)
    focused_window_id: str = Field(min_length=1, max_length=200)
    maximized_window_id: str | None = Field(default=None, max_length=200)
    referenced_symbols: list[WorkspaceSymbol] = Field(default_factory=list, max_length=5000)
    windows: list[WorkspaceWindowInput] = Field(min_length=1, max_length=8)
    attachments: list[WorkspaceAttachmentInput] = Field(default_factory=list, max_length=64)
    drawings_by_symbol: dict[WorkspaceSymbol, WorkspaceTrendLineList] = Field(
        default_factory=dict, max_length=16
    )


class WorkspaceContextService:
    def __init__(self, intraday_service: IntradayQuoteService | None) -> None:
        self._intraday_service = intraday_service
        self._lock = Lock()
        self._snapshot: dict[str, object] | None = None

    def publish(self, payload: WorkspaceContextInput) -> dict[str, object]:
        snapshot = payload.model_dump(mode="json")
        snapshot["received_at"] = datetime.now(UTC).isoformat()
        with self._lock:
            self._snapshot = snapshot
        return {
            "schema_version": snapshot["schema_version"],
            "received_at": snapshot["received_at"],
        }

    def get(self, store: SQLiteMarketDataStore) -> dict[str, object] | None:
        with self._lock:
            snapshot = deepcopy(self._snapshot)
        if snapshot is None:
            return None
        return self._enrich_latest_state(store, snapshot)

    def _enrich_latest_state(
        self, store: SQLiteMarketDataStore, snapshot: dict[str, object]
    ) -> dict[str, object]:
        windows = snapshot.get("windows", [])
        if not isinstance(windows, list):
            return snapshot
        for window in windows:
            if not isinstance(window, dict) or window.get("type") != "chart":
                continue
            instrument = window.get("instrument")
            if not isinstance(instrument, dict):
                continue
            requested_symbol = str(instrument.get("symbol", ""))
            summary = store.get_instrument_summary(requested_symbol) or {}
            symbol = str(summary.get("symbol") or requested_symbol.upper())
            if str(summary.get("kind", "")).startswith("futures-"):
                instrument.update({
                    key: summary.get(key)
                    for key in (
                        "symbol", "name", "kind", "exchange", "product_code",
                        "lifecycle_status", "contract_month", "series_kind",
                        "series_variant", "price_basis", "rule_version",
                    )
                })
                window["latest_data_state"] = self._futures_latest_state(
                    store, symbol
                )
                continue
            final_date = store.get_latest_daily_bar_date(symbol)
            final = None
            if final_date is not None:
                rows = store.get_daily_bars(symbol, final_date, final_date)
                if rows:
                    row = rows[-1]
                    final = {
                        "trade_date": row.trade_date.isoformat(),
                        "close": row.close,
                        "volume": row.volume,
                        "source": row.source,
                        "bar_state": "final",
                    }
            provisional = self._intraday_service.get(symbol) if self._intraday_service else None
            if provisional is not None and (
                final_date is None or provisional["trade_date"] > final_date
            ):
                provisional = {
                    "trade_date": provisional["trade_date"].isoformat(),
                    "close": provisional["close"],
                    "volume": provisional["volume"],
                    "source": provisional["source"],
                    "bar_state": "intraday",
                    "stale": provisional["stale"],
                    "provider_time": provisional["provider_time"],
                }
            else:
                provisional = None
            window["latest_data_state"] = {
                "effective": provisional or final,
                "latest_final": final,
                "provisional": provisional,
                "precedence": "provisional_after_latest_final" if provisional else "latest_final",
            }
        snapshot["latest_state_observed_at"] = datetime.now(UTC).isoformat()
        return snapshot

    def _futures_latest_state(
        self, store: SQLiteMarketDataStore, symbol: str
    ) -> dict[str, object]:
        final_date = store.get_latest_daily_bar_date(symbol)
        today = date.today()
        start_date = final_date or (today - timedelta(days=10))
        end_date = max(today, start_date)
        rows = store.list_fused_futures_daily_bars(symbol, start_date, end_date)
        final = next((item for item in reversed(rows) if item.state.value == "final"), None)
        provisional = next(
            (item for item in reversed(rows) if item.state.value == "provisional"),
            None,
        )

        def payload(row) -> dict[str, object] | None:
            if row is None:
                return None
            return {
                "trade_date": row.trading_day.isoformat(),
                "close": row.close,
                "volume": row.volume_contracts,
                "amount": row.amount,
                "settlement": row.settlement,
                "previous_settlement": row.previous_settlement,
                "open_interest": row.open_interest_contracts,
                "open_interest_change": row.open_interest_change_contracts,
                "mapped_contract_symbol": row.mapped_contract_symbol,
                "source": row.source,
                "bar_state": row.state.value,
                "stale": row.stale,
                "provider_time": (
                    row.provider_time.isoformat() if row.provider_time else None
                ),
            }

        final_payload = payload(final)
        provisional_payload = payload(provisional)
        return {
            "effective": provisional_payload or final_payload,
            "latest_final": final_payload,
            "provisional": provisional_payload,
            "precedence": (
                "provisional_after_latest_final"
                if provisional_payload else "latest_final"
            ),
        }
