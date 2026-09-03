"""Runtime dependencies shared by StockHarness API routers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from fastapi import Request

from stock_harness.config import FuturesExchangeCutoff
from stock_harness.futures_intraday import FuturesProvisionalService
from stock_harness.intraday import IntradayQuoteService
from stock_harness.models import AdjustmentFactor, StockTradeStatus
from stock_harness.workspace_context import WorkspaceContextService


FactorLoader = Callable[[list[str], date, date], list[AdjustmentFactor]]
StatusLoader = Callable[[list[str], date, date], list[StockTradeStatus]]


@dataclass(frozen=True, slots=True)
class ApiRuntime:
    workspace_context: WorkspaceContextService
    update_status: Callable[[], dict[str, object]] | None
    update_trigger: Callable[[], dict[str, object]] | None
    intraday_service: IntradayQuoteService | None
    futures_provisional_service: FuturesProvisionalService | None
    futures_final_cutoffs: tuple[FuturesExchangeCutoff, ...]
    now_provider: Callable[[], datetime]
    custom_index_factor_loader: FactorLoader | None
    custom_index_status_loader: StatusLoader | None


def runtime(request: Request) -> ApiRuntime:
    return request.app.state.api_runtime
