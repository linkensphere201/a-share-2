"""Bounded background refresh for completed daily market snapshots."""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

from stock_harness.config import RuntimeSettings, load_runtime_settings
from stock_harness.futures_provider import TushareFuturesProvider
from stock_harness.futures_update import run_futures_increment
from stock_harness.list_data import refresh_list_data
from stock_harness.models import InstrumentKind
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.tushare_provider import TushareDailyProvider


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UpdateResult:
    calendar_dates: int
    snapshots_checked: int
    snapshots_written: int
    rows_changed: int
    market_snapshot_rows: int
    etfs_checked: int
    etfs_completed: int
    holding_rows: int
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UpdateStatus:
    state: str = "idle"
    last_outcome: str | None = None
    trigger: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    next_run_at: str | None = None
    calendar_dates: int = 0
    snapshots_checked: int = 0
    snapshots_written: int = 0
    rows_changed: int = 0
    market_snapshot_rows: int = 0
    etfs_checked: int = 0
    etfs_completed: int = 0
    holding_rows: int = 0
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class IncrementalUpdater:
    def __init__(
        self,
        settings: RuntimeSettings,
        provider_factory: Callable[[], TushareDailyProvider] | None = None,
        futures_provider_factory: Callable[[], TushareFuturesProvider] | None = None,
    ) -> None:
        self.settings = settings
        self._provider_factory = provider_factory or (
            lambda: TushareDailyProvider(settings.tushare)
        )
        self._futures_provider_factory = futures_provider_factory or (
            lambda: TushareFuturesProvider(settings.futures.canonical)
        )

    def run_once(
        self, now: datetime | None = None, include_current_day: bool = False,
    ) -> UpdateResult:
        now = now or datetime.now()
        calendar_end = now.date()
        completed_end = (
            calendar_end
            if now.time() >= time(18, 0)
            or (include_current_day and now.time() >= time(15, 0))
            else calendar_end - timedelta(days=1)
        )
        start_date = calendar_end - timedelta(
            days=self.settings.auto_update.calendar_lookback_days
        )
        provider = self._provider_factory()
        errors: list[str] = []
        checked = written = changed = 0
        with _open_store(self.settings) as store:
            trading_dates = provider.trading_dates(start_date, calendar_end)
            store.upsert_trading_dates(provider.code, trading_dates)
            instruments = provider.list_instruments()
            store.upsert_instruments(instruments)

            datasets = self._load_datasets(
                provider, store, instruments, calendar_end, errors
            )
            open_dates = [item for item in trading_dates if item <= completed_end]
            for trade_date in open_dates:
                for source, scope, symbols, fetch in datasets:
                    checked += 1
                    if store.has_daily_snapshot(source, scope, trade_date):
                        continue
                    try:
                        bars = [bar for bar in fetch(trade_date) if bar.symbol in symbols]
                        rejected = tuple(
                            item for item in provider.rejected_bars
                            if item.symbol in symbols
                        )
                        if rejected:
                            for item in rejected:
                                store.record_provider_incident(
                                    source,
                                    "daily_ohlcv",
                                    scope,
                                    item.trade_date,
                                    "invalid_daily_bar",
                                    f"{item.symbol}: {item.reason}",
                                )
                            if bars:
                                changed += store.upsert_daily_bars(source, bars).changed
                            errors.append(
                                f"{scope} {trade_date}: {len(rejected)} invalid bars"
                            )
                            continue
                        if not bars:
                            store.record_coverage_gap(
                                source, scope, trade_date, "provider returned no daily snapshot"
                            )
                            errors.append(f"{scope} {trade_date}: empty snapshot")
                            continue
                        stats = store.upsert_daily_snapshot(source, scope, trade_date, bars)
                        store.resolve_provider_incident(
                            source,
                            "daily_ohlcv",
                            scope,
                            trade_date,
                            "invalid_daily_bar",
                            "configured-scope daily snapshot stored after provider retry",
                        )
                        written += 1
                        changed += stats.changed
                    except Exception as exc:
                        LOGGER.exception(
                            "auto_update_snapshot_failed source=%s scope=%s date=%s",
                            source,
                            scope,
                            trade_date,
                        )
                        errors.append(f"{scope} {trade_date}: {exc}")
            list_result = refresh_list_data(
                provider, store, self.settings, open_dates
            )
            errors.extend(list_result.errors)
            if changed > 0 or store.has_dirty_custom_indices():
                self._refresh_custom_indices(provider, store, completed_end, errors)
            if self.settings.futures.enabled and self.settings.futures.canonical.enabled:
                try:
                    futures_result = run_futures_increment(
                        self._futures_provider_factory(),
                        store,
                        self.settings.futures.exchanges,
                        completed_end,
                        self.settings.futures.correction_window_trading_days,
                    )
                    changed += futures_result.rows_changed
                    errors.extend(
                        f"futures: {item}" for item in futures_result.errors
                    )
                    LOGGER.info(
                        "futures_auto_update_complete exchanges=%d contracts_checked=%d "
                        "contracts_updated=%d rows_changed=%d mappings=%d errors=%d",
                        futures_result.exchanges,
                        futures_result.contracts_checked,
                        futures_result.contracts_updated,
                        futures_result.rows_changed,
                        futures_result.mappings_written,
                        len(futures_result.errors),
                    )
                except Exception as exc:
                    LOGGER.error(
                        "futures_auto_update_failed error_type=%s",
                        type(exc).__name__,
                    )
                    errors.append(f"futures: {type(exc).__name__}")
            store.checkpoint("PASSIVE")
        return UpdateResult(
            len(trading_dates), checked, written, changed,
            list_result.snapshot_rows, list_result.etfs_checked,
            list_result.etfs_completed, list_result.holding_rows, tuple(errors),
        )

    def _load_datasets(self, provider, store, instruments, observed_on, errors):
        datasets = []
        stock_symbols = {item.symbol for item in instruments}
        datasets.append(
            ("tushare", "stock", stock_symbols, provider.fetch_daily_bars)
        )

        def add_dataset(source, scope, instruments, fetch):
            store.upsert_instruments(instruments)
            datasets.append((source, scope, {item.symbol for item in instruments}, fetch))

        loaders = (
            (
                "expanded_etf",
                lambda: provider.list_all_equity_etfs(observed_on),
                lambda entries: (
                    store.upsert_catalog_entries(entries),
                    datasets.append((
                        "tushare", "expanded_etf",
                        {entry.instrument.symbol for entry in entries},
                        lambda day: provider.fetch_daily_snapshot(InstrumentKind.ETF, day),
                    )),
                ),
            ),
            (
                "index",
                lambda: provider.list_broad_indices(self.settings.universe.broad_indices),
                lambda items: add_dataset(
                    "tushare", "index", items,
                    lambda day: provider.fetch_daily_snapshot(InstrumentKind.INDEX, day),
                ),
            ),
            (
                "sector",
                lambda: provider.list_sectors(
                    self.settings.universe.sector_source, self.settings.universe.sector_level
                ),
                lambda items: add_dataset(
                    "tushare", "sector", items,
                    lambda day: provider.fetch_daily_snapshot(InstrumentKind.SECTOR, day),
                ),
            ),
            (
                "dc_board",
                lambda: provider.list_dc_boards(observed_on),
                lambda entries: (
                    store.upsert_catalog_entries(entries),
                    datasets.append((
                        "tushare_dc", "dc_board",
                        {entry.instrument.symbol for entry in entries},
                        lambda day: provider.fetch_board_daily_snapshot("eastmoney", day),
                    )),
                ),
            ),
            (
                "ths_board",
                lambda: provider.list_ths_boards(observed_on),
                lambda entries: (
                    store.upsert_catalog_entries(entries),
                    datasets.append((
                        "tushare_ths", "ths_board",
                        {entry.instrument.symbol for entry in entries},
                        lambda day: provider.fetch_board_daily_snapshot("ths", day),
                    )),
                ),
            ),
        )
        for name, load, register in loaders:
            try:
                register(load())
            except Exception as exc:
                LOGGER.exception("auto_update_catalog_failed scope=%s", name)
                errors.append(f"{name} catalog: {exc}")
        return datasets

    @staticmethod
    def _refresh_custom_indices(provider, store, completed_end, errors) -> None:
        summaries = store.list_custom_indices()
        if not summaries:
            return
        factor_starts: dict[str, date] = {}
        details = []
        for summary in summaries:
            detail = store.get_custom_index(str(summary["id"]))
            if detail is None:
                continue
            details.append(detail)
            last_date = detail["last_trade_date"]
            start_date = (
                last_date + timedelta(days=1)
                if last_date is not None
                else detail["base_date"]
            )
            if start_date > completed_end:
                continue
            for member in detail["members"]:
                symbol = str(member["symbol"])
                factor_starts[symbol] = min(
                    factor_starts.get(symbol, start_date), start_date
                )
        for symbol, start_date in factor_starts.items():
            try:
                factors = provider.fetch_adjustment_factors(
                    symbol, start_date, completed_end
                )
                store.upsert_adjustment_factors(provider.code, factors)
                statuses = provider.fetch_suspension_statuses(
                    symbol, start_date, completed_end
                )
                store.upsert_stock_trade_statuses(provider.code, statuses)
            except Exception as exc:
                LOGGER.exception(
                    "custom_index_factor_update_failed symbol=%s start=%s end=%s",
                    symbol, start_date, completed_end,
                )
                errors.append(f"custom index factor {symbol}: {exc}")
        for detail in details:
            try:
                store.rebuild_custom_index(str(detail["id"]), mode="incremental")
            except Exception as exc:
                LOGGER.exception(
                    "custom_index_incremental_update_failed index_id=%s",
                    detail["id"],
                )
                errors.append(f"custom index {detail['name']}: {exc}")


class AutoUpdateService:
    def __init__(self, updater: IncrementalUpdater, interval_seconds: int) -> None:
        self._updater = updater
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._manual_pending = False
        self._status = UpdateStatus()
        self._thread = threading.Thread(
            target=self._run, name="stock-harness-auto-update", daemon=True
        )

    @classmethod
    def from_config(cls, provider_config: Path, storage_config: Path) -> AutoUpdateService:
        settings = load_runtime_settings(provider_config, storage_config)
        return cls(
            IncrementalUpdater(settings), settings.auto_update.poll_interval_seconds
        )

    def start(self) -> None:
        if not self._thread.is_alive():
            LOGGER.info("auto_update_service_start interval_seconds=%s", self._interval_seconds)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=10.0)
        LOGGER.info("auto_update_service_stop")

    def status(self) -> dict[str, object]:
        with self._lock:
            return self._status.as_dict()

    def trigger(self) -> dict[str, object]:
        with self._lock:
            if self._status.state == "running":
                if self._status.trigger == "manual" or self._manual_pending:
                    LOGGER.info("auto_update_manual_request_coalesced state=running")
                    return {"accepted": False, "queued": self._manual_pending, **self._status.as_dict()}
                self._manual_pending = True
                result = {"accepted": True, "queued": True, **self._status.as_dict()}
                LOGGER.info("auto_update_manual_request_queued_after_current")
                self._wake.set()
                return result
            self._manual_pending = True
            self._status = UpdateStatus(state="queued", trigger="manual")
            result = {"accepted": True, **self._status.as_dict()}
        LOGGER.info("auto_update_manual_request_queued")
        self._wake.set()
        return result

    def _run(self) -> None:
        while not self._stop.is_set():
            started = datetime.now()
            with self._lock:
                trigger = "manual" if self._manual_pending else "scheduled"
                self._manual_pending = False
                self._status = UpdateStatus(
                    state="running", trigger=trigger, started_at=started.isoformat()
                )
            try:
                LOGGER.info(
                    "auto_update_run_start trigger=%s started_at=%s",
                    trigger, started.isoformat(),
                )
                result = self._updater.run_once(
                    started, include_current_day=trigger == "manual"
                )
                completed = datetime.now()
                next_run = completed + timedelta(seconds=self._interval_seconds)
                with self._lock:
                    manual_queued = self._manual_pending
                self._set_status(UpdateStatus(
                    state="queued" if manual_queued else ("warning" if result.errors else "idle"),
                    last_outcome=None if manual_queued else (
                        "partial" if result.errors else "completed"
                    ),
                    trigger="manual" if manual_queued else trigger,
                    started_at=started.isoformat(),
                    completed_at=completed.isoformat(),
                    next_run_at=next_run.isoformat(),
                    calendar_dates=result.calendar_dates,
                    snapshots_checked=result.snapshots_checked,
                    snapshots_written=result.snapshots_written,
                    rows_changed=result.rows_changed,
                    market_snapshot_rows=result.market_snapshot_rows,
                    etfs_checked=result.etfs_checked,
                    etfs_completed=result.etfs_completed,
                    holding_rows=result.holding_rows,
                    error="; ".join(result.errors[:5]) if result.errors else None,
                ))
                LOGGER.info(
                    "auto_update_run_complete trigger=%s state=%s outcome=%s "
                    "snapshots_written=%s rows_changed=%s errors=%s",
                    trigger,
                    "warning" if result.errors else "idle",
                    "partial" if result.errors else "completed",
                    result.snapshots_written,
                    result.rows_changed,
                    len(result.errors),
                )
                if result.errors:
                    LOGGER.warning(
                        "auto_update_completed_with_warnings count=%s errors=%s",
                        len(result.errors), "; ".join(result.errors[:5]),
                    )
            except Exception as exc:
                LOGGER.exception("auto_update_failed")
                completed = datetime.now()
                self._set_status(UpdateStatus(
                    state="error",
                    last_outcome="failed",
                    trigger=trigger,
                    started_at=started.isoformat(),
                    completed_at=completed.isoformat(),
                    next_run_at=(completed + timedelta(seconds=self._interval_seconds)).isoformat(),
                    error=str(exc),
                ))
            if self._stop.is_set():
                break
            self._wake.clear()
            with self._lock:
                manual_queued = self._manual_pending
            if not manual_queued:
                self._wake.wait(self._interval_seconds)
            if self._stop.is_set():
                break

    def _set_status(self, status: UpdateStatus) -> None:
        with self._lock:
            self._status = status


def _open_store(settings: RuntimeSettings) -> SQLiteMarketDataStore:
    return SQLiteMarketDataStore(
        settings.database_path,
        cache_size_kib=settings.sqlite_cache_size_kib,
        mmap_size_mib=settings.sqlite_mmap_size_mib,
        temp_store=settings.sqlite_temp_store,
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
    )
