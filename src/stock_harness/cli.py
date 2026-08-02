"""StockHarness data CLI."""

from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path

from stock_harness.backfill import run_stock_backfill, years_ago
from stock_harness.config import RuntimeSettings, load_runtime_settings
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.tushare_provider import TushareDailyProvider
from stock_harness.repair import repair_tushare_stock_date
from stock_harness.repair_providers import BaostockTradeDateRepairProvider
from stock_harness.validation import validate_symbols
from stock_harness.validation_providers import (
    AkShareEastmoneyValidationProvider,
    AkShareSinaValidationProvider,
    BaostockValidationProvider,
)


def _open_store(settings: RuntimeSettings) -> SQLiteMarketDataStore:
    return SQLiteMarketDataStore(
        settings.database_path,
        cache_size_kib=settings.sqlite_cache_size_kib,
        mmap_size_mib=settings.sqlite_mmap_size_mib,
        temp_store=settings.sqlite_temp_store,
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="stock-harness")
    parser.add_argument("--provider-config", type=Path, default=Path("config/providers.local.yaml"))
    parser.add_argument("--storage-config", type=Path, default=Path("config/storage.local.yaml"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe", help="Probe instruments, calendar, and one latest daily snapshot")
    probe.add_argument("--end-date", type=date.fromisoformat, default=date.today())

    backfill = subparsers.add_parser("backfill-stocks", help="Resume full-market stock daily backfill")
    backfill.add_argument("--years", type=int, default=30)
    backfill.add_argument("--start-date", type=date.fromisoformat)
    backfill.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    backfill.add_argument("--max-dates", type=int)
    backfill.add_argument("--progress-every", type=int, default=10)
    validate = subparsers.add_parser(
        "validate-date", help="Compare stored Tushare OHLCV with configured free providers"
    )
    validate.add_argument("--trade-date", type=date.fromisoformat, required=True)
    validate.add_argument("--symbol", action="append", dest="symbols")
    repair = subparsers.add_parser(
        "repair-gaps", help="Consume queued Tushare gaps through Baostock and AkShare"
    )
    repair.add_argument("--trade-date", type=date.fromisoformat, action="append", dest="dates")
    repair.add_argument("--limit-dates", type=int)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_runtime_settings(args.provider_config, args.storage_config)
    if args.command == "probe":
        provider = TushareDailyProvider(settings.tushare)
        instruments = provider.list_instruments()
        calendar = provider.trading_dates(years_ago(args.end_date, 1), args.end_date)
        if not calendar:
            raise RuntimeError("provider returned no open trading dates")
        bars = provider.fetch_daily_bars(calendar[-1])
        print(
            f"provider={provider.code} instruments={len(instruments)} "
            f"latest_trade_date={calendar[-1]} latest_rows={len(bars)}"
        )
        return
    if args.command == "validate-date":
        providers = []
        for provider_name in settings.validation.providers:
            if provider_name == "akshare":
                providers.append(AkShareEastmoneyValidationProvider())
            elif provider_name == "baostock":
                providers.append(BaostockValidationProvider())
            else:
                raise ValueError(f"unsupported validation provider: {provider_name}")
        symbols = tuple(args.symbols or settings.validation.sample_symbols)
        with _open_store(settings) as store:
            summary = validate_symbols(
                store,
                "tushare",
                providers,
                symbols,
                args.trade_date,
                settings.validation.price_abs_tolerance,
                settings.validation.volume_rel_tolerance,
            )
        print(
            f"trade_date={args.trade_date} checked={summary.checked} matched={summary.matched} "
            f"mismatched={summary.mismatched} missing={summary.missing} errors={summary.errors}"
        )
        return
    if args.command == "repair-gaps":
        if not settings.repair.enabled:
            raise ValueError("repair channel is disabled")
        if settings.repair.universe_provider != "baostock":
            raise ValueError(
                f"unsupported repair universe provider: {settings.repair.universe_provider}"
            )
        limit = args.limit_dates or settings.repair.max_dates_per_run
        with _open_store(settings) as store:
            dates = args.dates
            if not dates:
                jobs = store.list_repair_jobs({"queued", "partial", "failed"})
                jobs.sort(key=lambda job: (job.updated_at_ms, job.trade_date, job.job_id))
                dates = [job.trade_date for job in jobs[:limit]]
            if not dates:
                return
            fallback_providers = []
            for provider_name in settings.repair.fallback_providers:
                if provider_name in {"akshare", "akshare_eastmoney"}:
                    fallback_providers.append(AkShareEastmoneyValidationProvider())
                elif provider_name == "akshare_sina":
                    fallback_providers.append(AkShareSinaValidationProvider())
                else:
                    raise ValueError(f"unsupported repair fallback provider: {provider_name}")
            for trade_date in dates[:limit]:
                result = repair_tushare_stock_date(
                    store,
                    trade_date,
                    BaostockTradeDateRepairProvider(),
                    fallback_providers,
                    [BaostockValidationProvider(), *fallback_providers],
                )
                print(
                    f"job_id={result.job_id} trade_date={trade_date} status={result.status} "
                    f"expected={result.expected_rows} repaired={result.repaired_rows} "
                    f"unresolved={result.unresolved_rows}"
                )
        return
    provider = TushareDailyProvider(settings.tushare)
    start_date = args.start_date or years_ago(args.end_date, args.years)
    with _open_store(settings) as store:
        result = run_stock_backfill(
            provider,
            store,
            start_date,
            args.end_date,
            max_dates=args.max_dates,
            progress_every=args.progress_every,
        )
        checkpoint = store.checkpoint("PASSIVE")
    print(
        f"backfill_summary trading_dates={result.trading_dates} "
        f"skipped_dates={result.skipped_dates} completed_dates={result.completed_dates} "
        f"rows_written={result.rows_written} "
        f"discovered_instruments={result.discovered_instruments} "
        f"empty_dates={result.empty_dates}"
    )
    print(
        f"sqlite_checkpoint busy={checkpoint[0]} log_pages={checkpoint[1]} "
        f"checkpointed_pages={checkpoint[2]}"
    )


if __name__ == "__main__":
    main()
