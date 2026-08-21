"""Active-workspace bounded provisional futures refresh."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
import logging
import threading
from collections.abc import Iterable

from stock_harness.config import (
    FuturesProductSessionRule,
    FuturesProvisionalProviderSettings,
    FuturesSessionWindow,
)
from stock_harness.futures_provider_health import FuturesProviderMonitor
from stock_harness.futures_provisional_provider import futures_provider_contract_code
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
    ambiguous_count: int = 0
    received_count: int = 0
    stale_count: int = 0
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
        main_contract_reporter=None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.monitor = monitor
        self.canonical_source = canonical_source
        self.main_contract_reporter = main_contract_reporter
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._references: tuple[str, ...] = ()
        self._group_id: str | None = None
        self._ambiguity_signature: tuple[tuple[str, str, str], ...] = ()
        self._ambiguity_evidence: dict[str, dict[str, str]] = {}
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
        self,
        now: datetime | None = None,
        *,
        manual: bool = False,
        references: Iterable[str] | None = None,
    ) -> dict[str, object]:
        with self._refresh_lock:
            return self._refresh_once(now, manual=manual, references=references)

    def _refresh_once(
        self,
        now: datetime | None,
        *,
        manual: bool,
        references: Iterable[str] | None,
    ) -> dict[str, object]:
        now = _china_time(now or datetime.now(CHINA_TIME))
        with self._lock:
            active_references = self._references
            group_id = self._group_id
        if references is not None:
            active_references = tuple(sorted({
                item.strip() for item in references if item.strip()
            }))
            if len(active_references) > self.settings.max_contracts:
                raise ValueError(
                    f"at most {self.settings.max_contracts} futures references are allowed"
                )
        if not self.settings.enabled:
            return self._skip("disabled", active_references, group_id, manual=manual)
        if not active_references:
            return self._skip("no-references", active_references, group_id, manual=manual)

        resolved: dict[str, str | None] = {item: None for item in active_references}
        contracts_by_symbol: dict[str, FuturesContract] = {}
        trading_days: dict[str, date] = {}
        active_exchanges = 0
        resolved_candidates = 0
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
                self.canonical_source, active_references, trading_day
            )
            candidate_symbols = {
                item for item in exchange_resolution.values() if item is not None
            }
            for contract in self.store.get_futures_contracts(tuple(candidate_symbols)):
                if contract.exchange is not exchange:
                    continue
                resolved_candidates += 1
                contract_trading_day = futures_contract_session_trading_day(
                    contract,
                    now,
                    lambda value, exchange=exchange: self.store.next_futures_open_day(
                        self.canonical_source, exchange, value
                    ),
                    self.settings.session_rules,
                )
                if contract_trading_day is not None:
                    contracts_by_symbol[contract.symbol] = contract
                    trading_days[contract.symbol] = contract_trading_day
            for reference, contract_symbol in exchange_resolution.items():
                if contract_symbol in contracts_by_symbol:
                    resolved[reference] = contract_symbol

        ambiguous = self._reject_ambiguous_continuous_references(
            resolved, contracts_by_symbol
        )
        retained_symbols = {item for item in resolved.values() if item is not None}
        contracts_by_symbol = {
            symbol: contract for symbol, contract in contracts_by_symbol.items()
            if symbol in retained_symbols
        }
        trading_days = {
            symbol: trading_day for symbol, trading_day in trading_days.items()
            if symbol in retained_symbols
        }
        if not contracts_by_symbol:
            reason = (
                "mapping-ambiguous" if ambiguous else
                "market-closed" if active_exchanges == 0 or resolved_candidates else
                "unresolved"
            )
            result = self._skip(
                reason,
                active_references,
                group_id,
                len(active_references),
                ambiguous_count=len(ambiguous),
                manual=manual,
            )
            result.update({"resolved": resolved, "ambiguous": ambiguous})
            return result
        if len(contracts_by_symbol) > self.settings.max_contracts:
            raise ValueError("resolved futures contracts exceed configured maximum")

        received = 0
        stale_contracts: set[str] = set()
        requested_contracts = set(contracts_by_symbol)
        try:
            with self._lock:
                previous_success_at = self._status.last_success_at
                self._status = FuturesProvisionalStatus(
                    state="refreshing", group_id=group_id,
                    reference_count=len(active_references),
                    contract_count=len(contracts_by_symbol),
                    unresolved_count=sum(value is None for value in resolved.values()),
                    ambiguous_count=len(ambiguous),
                    last_attempt_at=now,
                    last_success_at=previous_success_at,
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
                stale_contracts.update(stale_symbols)
                self.store.upsert_futures_provisional_daily_bars(
                    self.monitor.provider.code, bars, now, stale_symbols
                )
                received += len(bars)
            with self._lock:
                self._status = FuturesProvisionalStatus(
                    state="stale" if stale_contracts else (
                        "empty" if received == 0 else
                        "partial" if received < len(requested_contracts) else "ready"
                    ), group_id=group_id,
                    reference_count=len(active_references),
                    contract_count=len(contracts_by_symbol),
                    unresolved_count=sum(value is None for value in resolved.values()),
                    ambiguous_count=len(ambiguous),
                    received_count=received,
                    stale_count=len(stale_contracts),
                    last_attempt_at=now,
                    last_success_at=now if received else previous_success_at,
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
                    reference_count=len(active_references),
                    contract_count=len(contracts_by_symbol),
                    unresolved_count=sum(value is None for value in resolved.values()),
                    ambiguous_count=len(ambiguous),
                    stale_count=len(contracts_by_symbol),
                    last_attempt_at=now, last_success_at=previous_success_at,
                    last_error=str(error),
                )
        result = self.status()
        result.update({
            "received": received, "resolved": resolved, "ambiguous": ambiguous,
        })
        return result

    def _reject_ambiguous_continuous_references(
        self,
        resolved: dict[str, str | None],
        contracts_by_symbol: dict[str, FuturesContract],
    ) -> dict[str, dict[str, str]]:
        if self.main_contract_reporter is None:
            return {}
        continuous: dict[str, FuturesContract] = {}
        for reference, contract_symbol in resolved.items():
            if contract_symbol is None:
                continue
            summary = self.store.get_instrument_summary(reference)
            if summary is not None and summary.get("kind") == "futures-continuous":
                continuous[reference] = contracts_by_symbol[contract_symbol]
        if not continuous:
            return {}
        with self._lock:
            previous_evidence = dict(self._ambiguity_evidence)
        try:
            update_names = getattr(
                self.main_contract_reporter, "update_product_display_names", None
            )
            if update_names is not None:
                update_names({
                    item.symbol: item.display_name
                    for item in self.store.list_futures_products()
                })
            reported = self.main_contract_reporter.report(tuple(continuous.values()))
        except Exception as error:
            LOGGER.debug("futures_main_contract_evidence_unavailable error=%s", error)
            ambiguous = {
                reference: evidence
                for reference, contract in continuous.items()
                if (evidence := previous_evidence.get(reference)) is not None
                and evidence["mapped_contract"] == contract.symbol
            }
            for reference in ambiguous:
                resolved[reference] = None
            return self._record_ambiguity_state(ambiguous)
        ambiguous: dict[str, dict[str, str]] = {}
        for reference, contract in continuous.items():
            provider_code = reported.get(contract.product_symbol)
            mapped_code = futures_provider_contract_code(contract)
            if provider_code is None:
                evidence = previous_evidence.get(reference)
                if evidence is not None and evidence["mapped_contract"] == contract.symbol:
                    ambiguous[reference] = evidence
                    resolved[reference] = None
                continue
            if provider_code.upper() == mapped_code.upper():
                continue
            ambiguous[reference] = {
                "mapped_contract": contract.symbol,
                "mapped_provider_contract": mapped_code,
                "reported_provider_contract": provider_code,
            }
            resolved[reference] = None
        return self._record_ambiguity_state(ambiguous)

    def _record_ambiguity_state(
        self, ambiguous: dict[str, dict[str, str]]
    ) -> dict[str, dict[str, str]]:
        signature = tuple(sorted(
            (
                reference,
                evidence["mapped_provider_contract"],
                evidence["reported_provider_contract"],
            )
            for reference, evidence in ambiguous.items()
        ))
        with self._lock:
            previous_signature = self._ambiguity_signature
            self._ambiguity_signature = signature
            self._ambiguity_evidence = dict(ambiguous)
        if signature and signature != previous_signature:
            LOGGER.warning(
                "futures_main_contract_mapping_ambiguous references=%d details=%s",
                len(ambiguous), ambiguous,
            )
        elif not signature and previous_signature:
            LOGGER.info("futures_main_contract_mapping_recovered")
        return ambiguous

    def _skip(
        self,
        reason: str,
        references: tuple[str, ...],
        group_id: str | None,
        unresolved_count: int = 0,
        ambiguous_count: int = 0,
        *,
        manual: bool = False,
    ) -> dict[str, object]:
        with self._lock:
            self._status = FuturesProvisionalStatus(
                state="idle" if reason == "no-references" else "skipped",
                group_id=group_id, reference_count=len(references),
                unresolved_count=unresolved_count,
                ambiguous_count=ambiguous_count,
                skip_reason=reason,
            )
        if manual:
            LOGGER.info(
                "futures_provisional_manual_refresh_skipped reason=%s references=%d",
                reason,
                len(references),
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


def futures_contract_session_trading_day(
    contract: FuturesContract,
    observed_at: datetime,
    next_open_day,
    rules: tuple[FuturesProductSessionRule, ...],
) -> date | None:
    observed_at = _china_time(observed_at)
    product_code = contract.product_symbol.rsplit(":", 1)[-1].upper()
    exact = next(
        (
            item for item in rules
            if item.exchange is contract.exchange and item.product_code == product_code
        ),
        None,
    )
    wildcard = next(
        (
            item for item in rules
            if item.exchange is contract.exchange and item.product_code == "*"
        ),
        None,
    )
    if exact is None and wildcard is None:
        return None
    day_windows = exact.day if exact is not None and exact.day else (
        wildcard.day if wildcard is not None else ()
    )
    night_windows = exact.night if exact is not None else (
        wildcard.night if wildcard is not None else ()
    )
    current_time = observed_at.time().replace(tzinfo=None)
    calendar_day = observed_at.date()
    if _inside_windows(current_time, day_windows):
        trading_day = next_open_day(calendar_day)
        return trading_day if trading_day == calendar_day else None
    if _inside_windows(current_time, night_windows):
        trading_day = next_open_day(calendar_day)
        if trading_day is None or (trading_day - calendar_day).days > 3:
            return None
        return trading_day
    return None


def _inside_windows(current: time, windows: tuple[FuturesSessionWindow, ...]) -> bool:
    return any(
        start <= current <= end
        if start < end
        else current >= start or current <= end
        for start, end in ((item.start, item.end) for item in windows)
    )


def _china_time(value: datetime) -> datetime:
    return value.replace(tzinfo=CHINA_TIME) if value.tzinfo is None else value.astimezone(CHINA_TIME)
