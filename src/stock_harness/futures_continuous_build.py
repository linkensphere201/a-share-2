"""Resumable coordinator for materialized futures continuous series."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import logging

from stock_harness.futures_continuous import materialize_continuous
from stock_harness.models import FuturesExchange, FuturesPriceBasis
from stock_harness.sqlite_store import SQLiteMarketDataStore


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FuturesContinuousBuildResult:
    selected_series: int
    completed_series: int
    skipped_clean_series: int
    skipped_unmapped_series: int
    rows_written: int
    roll_events_written: int
    warning_count: int
    errors: tuple[str, ...]


def build_futures_continuous_series(
    store: SQLiteMarketDataStore,
    source: str,
    start_date: date,
    end_date: date,
    *,
    exchanges: tuple[FuturesExchange, ...] = (),
    max_series: int | None = None,
    force: bool = False,
) -> FuturesContinuousBuildResult:
    if start_date > end_date:
        raise ValueError("continuous build start must not exceed end")
    if max_series is not None and max_series < 1:
        raise ValueError("continuous build max_series must be positive")
    selected_exchanges = set(exchanges)
    candidates = [
        item for item in store.list_futures_continuous_series()
        if not selected_exchanges or item.exchange in selected_exchanges
    ]
    if max_series is not None:
        candidates = candidates[:max_series]
    LOGGER.info(
        "futures_continuous_build_started series=%d start=%s end=%s force=%s",
        len(candidates), start_date, end_date, force,
    )
    completed = clean = unmapped = rows_written = rolls_written = warnings = 0
    errors: list[str] = []
    for index, series in enumerate(candidates, start=1):
        existing = store.get_futures_continuous_build_status(series.symbol)
        dirty = store.get_futures_continuous_dirty_state(series.symbol)
        if not force and existing is not None and dirty is None:
            clean += 1
            continue
        rebuilt_from = None
        materialize_from = None
        input_start = start_date
        if (
            not force
            and existing is not None
            and dirty is not None
            and series.price_basis is FuturesPriceBasis.RAW
        ):
            rebuilt_from = max(start_date, dirty["dirty_from"])
            materialize_from = rebuilt_from
            prior = store.get_latest_futures_daily_bar_before(
                series.symbol, rebuilt_from
            )
            if prior is not None:
                input_start = prior.trading_day
        mappings = store.list_futures_roll_mappings_for_build(
            series.symbol, input_start, end_date
        )
        if not mappings:
            unmapped += 1
            continue
        try:
            contract_bars = []
            for symbol in sorted({item.contract_symbol for item in mappings}):
                contract_bars.extend(
                    store.list_futures_daily_bars(symbol, input_start, end_date)
                )
            build = materialize_continuous(
                series, mappings, contract_bars,
                start_date=materialize_from, end_date=end_date,
            )
            result = store.persist_futures_continuous_build(
                source, build, rebuilt_from=rebuilt_from,
            )
            completed += 1
            rows_written += int(result["rows"])
            rolls_written += int(result["rolls"])
            warnings += int(result["warnings"])
        except Exception as exc:
            log = LOGGER.warning if len(errors) < 10 or (len(errors) + 1) % 50 == 0 else LOGGER.debug
            log(
                "futures_continuous_build_failed symbol=%s error_type=%s error_count=%d",
                series.symbol, type(exc).__name__, len(errors) + 1,
            )
            errors.append(f"{series.symbol}: {type(exc).__name__}")
        if index % 25 == 0 or index == len(candidates):
            LOGGER.info(
                "futures_continuous_build_progress processed=%d total=%d completed=%d clean=%d unmapped=%d rows=%d errors=%d",
                index, len(candidates), completed, clean, unmapped, rows_written,
                len(errors),
            )
    result = FuturesContinuousBuildResult(
        selected_series=len(candidates),
        completed_series=completed,
        skipped_clean_series=clean,
        skipped_unmapped_series=unmapped,
        rows_written=rows_written,
        roll_events_written=rolls_written,
        warning_count=warnings,
        errors=tuple(errors),
    )
    LOGGER.info(
        "futures_continuous_build_completed selected=%d completed=%d clean=%d unmapped=%d rows=%d rolls=%d warnings=%d errors=%d",
        result.selected_series, result.completed_series,
        result.skipped_clean_series, result.skipped_unmapped_series,
        result.rows_written, result.roll_events_written,
        result.warning_count, len(result.errors),
    )
    return result
