"""Bounded health and diagnostics for futures Provider calls."""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from urllib.error import HTTPError, URLError

from stock_harness.models import FuturesContract, FuturesDailyBar


LOGGER = logging.getLogger(__name__)
CHINA_TIME = timezone(timedelta(hours=8))


class FuturesProviderIssueKind(StrEnum):
    PERMISSION = "permission"
    RATE_LIMIT = "rate-limit"
    NETWORK = "network"
    FIELD_CONTRACT = "field-contract"
    MALFORMED_ROW = "malformed-row"
    EMPTY_RESPONSE = "empty-response"
    STALE_QUOTE = "stale-quote"
    PARTIAL_RESULT = "partial-result"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class FuturesProviderHealth:
    provider: str
    state: str
    requested_count: int
    received_count: int
    missing_count: int
    stale_count: int
    consecutive_failures: int
    last_issue: FuturesProviderIssueKind | None
    last_error: str | None
    last_attempt_at: datetime | None
    last_success_at: datetime | None


class FuturesProviderMonitor:
    """Classify failures and rate-limit diagnostics without changing quote data."""

    def __init__(
        self,
        provider,
        stale_after_seconds: int,
        warning_interval_seconds: int = 60,
    ) -> None:
        self.provider = provider
        self.stale_after_seconds = max(1, stale_after_seconds)
        self.warning_interval_seconds = max(1, warning_interval_seconds)
        self._lock = threading.RLock()
        self._warning_last_at: dict[str, datetime] = {}
        self._health = FuturesProviderHealth(
            provider=provider.code,
            state="idle",
            requested_count=0,
            received_count=0,
            missing_count=0,
            stale_count=0,
            consecutive_failures=0,
            last_issue=None,
            last_error=None,
            last_attempt_at=None,
            last_success_at=None,
        )

    def fetch(
        self,
        contracts: Sequence[FuturesContract],
        expected_trading_day: date | None = None,
        observed_at: datetime | None = None,
    ) -> tuple[FuturesDailyBar, ...]:
        now = _localized(observed_at or datetime.now(CHINA_TIME))
        with self._lock:
            previous_state = self._health.state
            failures = self._health.consecutive_failures
            self._health = FuturesProviderHealth(
                provider=self.provider.code,
                state="checking",
                requested_count=len(contracts),
                received_count=0,
                missing_count=0,
                stale_count=0,
                consecutive_failures=failures,
                last_issue=self._health.last_issue,
                last_error=self._health.last_error,
                last_attempt_at=now,
                last_success_at=self._health.last_success_at,
            )
        try:
            bars = tuple(
                self.provider.fetch(contracts, expected_trading_day, observed_at=now)
            )
        except Exception as error:
            issue = classify_futures_provider_error(error)
            message = type(error).__name__
            with self._lock:
                self._health = FuturesProviderHealth(
                    provider=self.provider.code,
                    state="error",
                    requested_count=len(contracts),
                    received_count=0,
                    missing_count=len(contracts),
                    stale_count=0,
                    consecutive_failures=failures + 1,
                    last_issue=issue,
                    last_error=message,
                    last_attempt_at=now,
                    last_success_at=self._health.last_success_at,
                )
            self._warn(issue, now, len(contracts), 0, message)
            raise

        missing = tuple(getattr(self.provider, "missing_contracts", ()))
        stale = tuple(
            item.symbol for item in bars
            if item.provider_time is not None
            and (now - _localized(item.provider_time)).total_seconds()
            > self.stale_after_seconds
        )
        issue: FuturesProviderIssueKind | None = None
        state = "ready"
        if not bars and contracts:
            issue = FuturesProviderIssueKind.EMPTY_RESPONSE
            state = "empty"
        elif missing:
            issue = FuturesProviderIssueKind.PARTIAL_RESULT
            state = "partial"
        elif stale:
            issue = FuturesProviderIssueKind.STALE_QUOTE
            state = "stale"
        with self._lock:
            self._health = FuturesProviderHealth(
                provider=self.provider.code,
                state=state,
                requested_count=len(contracts),
                received_count=len(bars),
                missing_count=len(missing),
                stale_count=len(stale),
                consecutive_failures=0,
                last_issue=issue,
                last_error=None,
                last_attempt_at=now,
                last_success_at=now if bars else self._health.last_success_at,
            )
        if issue is not None:
            detail = (
                f"missing={len(missing)} stale={len(stale)} "
                f"symbols={','.join((missing or stale)[:10])}"
            )
            self._warn(issue, now, len(contracts), len(bars), detail)
        elif previous_state not in {"idle", "checking", "ready"}:
            LOGGER.info(
                "futures_provider_recovered provider=%s previous_state=%s received=%s",
                self.provider.code,
                previous_state,
                len(bars),
            )
        return bars

    def status(self) -> dict[str, object]:
        with self._lock:
            return asdict(self._health)

    def _warn(
        self,
        issue: FuturesProviderIssueKind,
        now: datetime,
        requested: int,
        received: int,
        detail: str,
    ) -> None:
        key = issue.value
        with self._lock:
            previous = self._warning_last_at.get(key)
            if previous is not None and (now - previous).total_seconds() < self.warning_interval_seconds:
                return
            self._warning_last_at[key] = now
        LOGGER.warning(
            "futures_provider_warning provider=%s issue=%s requested=%s received=%s detail=%s",
            self.provider.code,
            issue.value,
            requested,
            received,
            detail,
        )


def classify_futures_provider_error(error: Exception) -> FuturesProviderIssueKind:
    if isinstance(error, HTTPError):
        if error.code in {401, 403}:
            return FuturesProviderIssueKind.PERMISSION
        if error.code == 429:
            return FuturesProviderIssueKind.RATE_LIMIT
        return FuturesProviderIssueKind.NETWORK
    if isinstance(error, (TimeoutError, URLError, ConnectionError)):
        return FuturesProviderIssueKind.NETWORK
    message = str(error).lower()
    if any(token in message for token in ("rate limit", "too many requests", "频率")):
        return FuturesProviderIssueKind.RATE_LIMIT
    if any(token in message for token in ("permission", "forbidden", "unauthorized", "权限")):
        return FuturesProviderIssueKind.PERMISSION
    if isinstance(error, ValueError):
        if any(token in message for token in (" row ", "invalid ", "must be positive")):
            return FuturesProviderIssueKind.MALFORMED_ROW
        return FuturesProviderIssueKind.FIELD_CONTRACT
    if isinstance(error, OSError):
        return FuturesProviderIssueKind.NETWORK
    return FuturesProviderIssueKind.UNKNOWN


def _localized(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=CHINA_TIME)
