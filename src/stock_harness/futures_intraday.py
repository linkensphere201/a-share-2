"""Active-workspace bounded provisional futures refresh."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
import logging
import threading
from collections.abc import Iterable

from stock_harness.config import FuturesProvisionalProviderSettings
from stock_harness.futures_provider_health import FuturesProviderMonitor
from stock_harness.models import FuturesContract, FuturesExchange
from stock_harness.sqlite_store import SQLiteMarketDataStore


LOGGER = logging.getLogger(__name__)
CHINA_TIME = timezone(timedelta(hours=8))


@dataclass(frozen=True, slots=True)
class FuturesProvisionalStatus:
    state: str = "idle"
    group_id: str | None = None
    reference_count: int = 0
    contract_count: int = 0
    unresolved_count: int = 0
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    skip_reason: str | None = None


class FuturesProvisionalService:
    def __init__(
        self,
        settings: FuturesProvisionalProviderSettings,
        store: SQLiteMarketDataStore,
        monitor: FuturesProviderMonitor,
        canonical_source: str = "tushare-futures",
    ) -> None:
        self.settings = settings
        self.store = store
        self.monitor = monitor
        self.canonical_source = canonical_source
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._references: tuple[str, ...] = ()
        self._group_id: str | None = None
        self._status = FuturesProvisionalStatus()
        self._thread = threading.Thread(
            target=self._run, name="stock-harness-futures-provisional", daemon=True
        )

    def start(self) -> None:
        if self.settings.enabled and not self._thread.is_alive():
            self._thread.start()
            LOGGER.info(
                "futures_provisional_service_start interval_seconds=%d",
                self.settings.refresh_interval_seconds,
            )

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=self.settings.request_timeout_seconds + 5)
        LOGGER.info("futures_provisional_service_stop")

    def subscribe(self, group_id: str, references: Iterable[str]) -> dict[str, object]:
        normalized = tuple(sorted({item.strip() for item in references if item.strip()}))
        if len(normalized) > self.settings.max_contracts:
            raise ValueError(
                f"at most {self.settings.max_contracts} futures references are allowed"
            )
        with self._lock:
            self._group_id = group_id
            self._references = normalized
            self._status = FuturesProvisionalStatus(
                state="idle", group_id=group_id, reference_count=len(normalized)
            )
        self._wake.set()
        LOGGER.info(
            "futures_provisional_subscription_changed group_id=%s references=%d",
            group_id, len(normalized),
        )
        return self.status()

    def status(self) -> dict[str, object]:
        with self._lock:
            payload = asdict(self._status)
        payload["enabled"] = self.settings.enabled
        payload["provider"] = self.monitor.provider.code
        return payload

    def refresh_once(
        self, now: datetime | None = None, *, manual: bool = False
    ) -> dict[str, object]:
        now = _china_time(now or datetime.now(CHINA_TIME))
        with self._refresh_lock, self._lock:
            references = self._references
            group_id = self._group_id
        if not self.settings.enabled:
            return self._skip("disabled", references, group_id)
        if not references:
            return self._skip("no-references", references, group_id)

        resolved: dict[str, str | None] = {item: None for item in references}
        contracts_by_symbol: dict[str, FuturesContract] = {}
        trading_days: dict[str, date] = {}
        active_exchanges = 0
        for exchange in FuturesExchange:
            trading_day = futures_session_trading_day(
                exchange,
                now,
                lambda value, exchange=exchange: self.store.next_futures_open_day(
                    self.canonical_source, exchange, value
                ),
            )
            if trading_day is None:
                continue
            active_exchanges += 1
            exchange_resolution = self.store.resolve_futures_contract_references(
                self.canonical_source, references, trading_day
            )
            candidate_symbols = {
                item for item in exchange_resolution.values() if item is not None
            }
            for contract in self.store.get_futures_contracts(tuple(candidate_symbols)):
                if contract.exchange is exchange:
                    contracts_by_symbol[contract.symbol] = contract
                    trading_days[contract.symbol] = trading_day
            for reference, contract_symbol in exchange_resolution.items():
                if contract_symbol in contracts_by_symbol:
                    resolved[reference] = contract_symbol

        if not contracts_by_symbol:
            reason = "market-closed" if active_exchanges == 0 else "unresolved"
            return self._skip(reason, references, group_id, len(references))
        if len(contracts_by_symbol) > self.settings.max_contracts:
            raise ValueError("resolved futures contracts exceed configured maximum")

        received = 0
        try:
            with self._lock:
                self._status = FuturesProvisionalStatus(
                    state="refreshing", group_id=group_id,
                    reference_count=len(references),
                    contract_count=len(contracts_by_symbol),
                    unresolved_count=sum(value is None for value in resolved.values()),
                    last_attempt_at=now,
                )
            grouped: dict[date, list[FuturesContract]] = {}
            for symbol, contract in contracts_by_symbol.items():
                grouped.setdefault(trading_days[symbol], []).append(contract)
            for trading_day, contracts in grouped.items():
                bars = self.monitor.fetch(contracts, trading_day, observed_at=now)
                stale_symbols = [
                    item.symbol for item in bars
                    if item.provider_time is not None
                    and (now - _china_time(item.provider_time)).total_seconds()
                    > self.settings.stale_after_seconds
                ]
                self.store.upsert_futures_provisional_daily_bars(
                    self.monitor.provider.code, bars, now, stale_symbols
                )
                received += len(bars)
            with self._lock:
                self._status = FuturesProvisionalStatus(
                    state="ready", group_id=group_id,
                    reference_count=len(references),
                    contract_count=len(contracts_by_symbol),
                    unresolved_count=sum(value is None for value in resolved.values()),
                    last_attempt_at=now, last_success_at=now,
                )
        except Exception as error:
            try:
                self.store.mark_futures_provisional_stale(
                    self.monitor.provider.code, tuple(contracts_by_symbol)
                )
            except Exception:
                LOGGER.warning(
                    "futures_provisional_stale_mark_failed contracts=%d",
                    len(contracts_by_symbol), exc_info=True,
                )
            LOGGER.debug(
                "futures_provisional_refresh_failed contracts=%d manual=%s error=%s",
                len(contracts_by_symbol), manual, error,
            )
            with self._lock:
                self._status = FuturesProvisionalStatus(
                    state="error", group_id=group_id,
                    reference_count=len(references),
                    contract_count=len(contracts_by_symbol),
                    unresolved_count=sum(value is None for value in resolved.values()),
                    last_attempt_at=now, last_error=str(error),
                )
        result = self.status()
        result.update({"received": received, "resolved": resolved})
        return result

    def _skip(
        self,
        reason: str,
        references: tuple[str, ...],
        group_id: str | None,
        unresolved_count: int = 0,
    ) -> dict[str, object]:
        with self._lock:
            self._status = FuturesProvisionalStatus(
                state="idle" if reason == "no-references" else "skipped",
                group_id=group_id, reference_count=len(references),
                unresolved_count=unresolved_count, skip_reason=reason,
            )
        return self.status()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.settings.refresh_interval_seconds)
            self._wake.clear()
            if self._stop.is_set():
                break
            self.refresh_once()


def futures_session_trading_day(
    exchange: FuturesExchange,
    observed_at: datetime,
    next_open_day,
) -> date | None:
    observed_at = _china_time(observed_at)
    current_time = observed_at.time().replace(tzinfo=None)
    calendar_day = observed_at.date()
    if exchange is FuturesExchange.CFFEX:
        active = (
            time(9, 15) <= current_time <= time(11, 30)
            or time(13, 0) <= current_time <= time(15, 15)
        )
        trading_day = next_open_day(calendar_day) if active else None
        return trading_day if trading_day == calendar_day else None
    day_active = (
        time(9, 0) <= current_time <= time(11, 30)
        or time(13, 30) <= current_time <= time(15, 0)
    )
    if day_active:
        trading_day = next_open_day(calendar_day)
        return trading_day if trading_day == calendar_day else None
    night_active = current_time >= time(21, 0) or current_time <= time(2, 30)
    return next_open_day(calendar_day) if night_active else None


def _china_time(value: datetime) -> datetime:
    return value.replace(tzinfo=CHINA_TIME) if value.tzinfo is None else value.astimezone(CHINA_TIME)
