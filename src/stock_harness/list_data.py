"""Incremental market-list snapshots and dated ETF composition refresh."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Sequence

from stock_harness.config import RuntimeSettings, load_runtime_settings
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.tushare_provider import TushareDailyProvider


ETF_HOLDING_SOURCE = "tushare_etf_pcf"


@dataclass(frozen=True, slots=True)
class ListDataRefreshResult:
    snapshot_rows: int
    etfs_checked: int
    etfs_completed: int
    holding_rows: int
    errors: tuple[str, ...]


def refresh_list_data(
    provider: TushareDailyProvider,
    store: SQLiteMarketDataStore,
    settings: RuntimeSettings,
    open_dates: Sequence[date],
    max_etfs: int | None = None,
) -> ListDataRefreshResult:
    if not open_dates:
        return ListDataRefreshResult(0, 0, 0, 0, ())
    ordered_dates = sorted(set(open_dates), reverse=True)
    errors: list[str] = []
    latest = ordered_dates[0]
    stock_snapshots = []
    for candidate in ordered_dates:
        try:
            stock_snapshots = list(provider.fetch_stock_market_snapshots(candidate))
        except Exception as exc:
            errors.append(f"market snapshots {candidate}: {exc}")
            break
        if stock_snapshots:
            latest = candidate
            break
    snapshot_rows = store.derive_market_snapshots(latest)
    if stock_snapshots:
        snapshot_rows += store.upsert_market_snapshots(provider.code, stock_snapshots)
    else:
        errors.append(f"market snapshots unavailable through {ordered_dates[-1]}")

    if not settings.etf_holdings.enabled:
        return ListDataRefreshResult(snapshot_rows, 0, 0, 0, tuple(errors))

    limit = max_etfs if max_etfs is not None else settings.etf_holdings.max_symbols_per_run
    preferred = [item.symbol for item in settings.universe.etfs]
    symbols = store.list_etfs_needing_holding_refresh(
        ETF_HOLDING_SOURCE, latest, limit, preferred
    )
    effective_offset = ordered_dates.index(latest)
    candidate_dates = ordered_dates[
        effective_offset : effective_offset + settings.etf_holdings.lookback_open_dates
    ]
    completed = holding_rows = 0
    for symbol in symbols:
        try:
            as_of_date, holdings = provider.fetch_etf_holdings(symbol, candidate_dates)
            if as_of_date is not None:
                holding_rows += store.replace_etf_holdings(
                    ETF_HOLDING_SOURCE, symbol, as_of_date, holdings
                )
            store.record_etf_holding_receipt(
                ETF_HOLDING_SOURCE, symbol, latest, as_of_date, len(holdings)
            )
            completed += 1
        except Exception as exc:
            errors.append(f"ETF holdings {symbol}: {exc}")
    return ListDataRefreshResult(
        snapshot_rows, len(symbols), completed, holding_rows, tuple(errors)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh list snapshots and ETF compositions")
    parser.add_argument("--provider-config", default="config/providers.local.yaml")
    parser.add_argument("--storage-config", default="config/storage.local.yaml")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--max-etfs", type=int)
    parser.add_argument("--all-etfs", action="store_true")
    args = parser.parse_args()
    settings = load_runtime_settings(Path(args.provider_config), Path(args.storage_config))
    provider = TushareDailyProvider(settings.tushare)
    start = args.as_of - timedelta(days=max(14, settings.auto_update.calendar_lookback_days))
    open_dates = provider.trading_dates(start, args.as_of)
    max_etfs = 100_000 if args.all_etfs else args.max_etfs
    with SQLiteMarketDataStore(
        settings.database_path,
        cache_size_kib=settings.sqlite_cache_size_kib,
        mmap_size_mib=settings.sqlite_mmap_size_mib,
        temp_store=settings.sqlite_temp_store,
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
    ) as store:
        result = refresh_list_data(provider, store, settings, open_dates, max_etfs)
        store.checkpoint("PASSIVE")
    print(
        "list_data_refresh "
        f"snapshot_rows={result.snapshot_rows} etfs_checked={result.etfs_checked} "
        f"etfs_completed={result.etfs_completed} holding_rows={result.holding_rows} "
        f"errors={len(result.errors)}"
    )
    for error in result.errors:
        print(f"error={error}")
    return 0 if not result.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
