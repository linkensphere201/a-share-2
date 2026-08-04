"""Bounded background refresh for completed daily market snapshots."""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

from stock_harness.config import RuntimeSettings, load_runtime_settings
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
    ) -> None:
        self.settings = settings
        self._provider_factory = provider_factory or (
            lambda: TushareDailyProvider(settings.tushare)
        )

    def run_once(self, now: datetime | None = None) -> UpdateResult:
        now = now or datetime.now()
        calendar_end = now.date()
        completed_end = calendar_end if now.time() >= time(18, 0) else calendar_end - timedelta(days=1)
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
                        rejected = provider.rejected_bars
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


class AutoUpdateService:
    def __init__(self, updater: IncrementalUpdater, interval_seconds: int) -> None:
        self._updater = updater
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._lock = threading.Lock()
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
        if self._thread.is_alive():
            self._thread.join(timeout=10.0)
        LOGGER.info("auto_update_service_stop")

    def status(self) -> dict[str, object]:
        with self._lock:
            return self._status.as_dict()

    def _run(self) -> None:
        while not self._stop.is_set():
            started = datetime.now()
            self._set_status(UpdateStatus(state="running", started_at=started.isoformat()))
            try:
                LOGGER.info("auto_update_run_start started_at=%s", started.isoformat())
                result = self._updater.run_once(started)
                completed = datetime.now()
                next_run = completed + timedelta(seconds=self._interval_seconds)
                self._set_status(UpdateStatus(
                    state="warning" if result.errors else "idle",
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
                    "auto_update_run_complete state=%s snapshots_written=%s rows_changed=%s errors=%s",
                    "warning" if result.errors else "idle",
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
                    started_at=started.isoformat(),
                    completed_at=completed.isoformat(),
                    next_run_at=(completed + timedelta(seconds=self._interval_seconds)).isoformat(),
                    error=str(exc),
                ))
            if self._stop.wait(self._interval_seconds):
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
