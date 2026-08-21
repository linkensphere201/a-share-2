"""StockHarness data CLI."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import logging
from datetime import date, timedelta
from pathlib import Path

from stock_harness.backfill import run_stock_backfill, run_symbol_backfill, years_ago
from stock_harness.config import RuntimeSettings, load_runtime_settings
from stock_harness.futures_provider import TushareFuturesProvider
from stock_harness.futures_backfill import run_futures_backfill
from stock_harness.futures_provider_health import FuturesProviderMonitor
from stock_harness.futures_provisional_provider import (
    AkShareFuturesRealtimeProvider,
    AkShareFuturesSpotProvider,
)
from stock_harness.futures_validation import (
    AkShareExchangeFuturesValidationProvider,
    AkShareSinaFuturesValidationProvider,
    compare_futures_provisional_takeovers,
    validate_futures_contracts,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.tushare_provider import TushareBoardDailyProvider, TushareDailyProvider
from stock_harness.models import FuturesExchange, FuturesLifecycleStatus, InstrumentKind
from stock_harness.repair import repair_tushare_stock_date
from stock_harness.repair_providers import BaostockTradeDateRepairProvider
from stock_harness.validation import validate_symbols
from stock_harness.validation_providers import (
    AkShareEastmoneyValidationProvider,
    AkShareEastmoneyBoardValidationProvider,
    AkShareSinaValidationProvider,
    AkShareSwValidationProvider,
    AkShareThsBoardValidationProvider,
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


def _catalog_entries(
    provider: TushareDailyProvider, scope: str, observed_on: date
):
    if scope == "etf":
        return provider.list_all_equity_etfs(observed_on)
    if scope == "dc":
        return provider.list_dc_boards(observed_on)
    if scope == "ths":
        return provider.list_ths_boards(observed_on)
    raise ValueError(f"unsupported catalog scope: {scope}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="stock-harness")
    parser.add_argument("--provider-config", type=Path, default=Path("config/providers.local.yaml"))
    parser.add_argument("--storage-config", type=Path, default=Path("config/storage.local.yaml"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe", help="Probe instruments, calendar, and one latest daily snapshot")
    probe.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    futures_probe = subparsers.add_parser(
        "probe-futures",
        help="Probe configured futures contracts, calendars, final daily data, and mappings",
    )
    futures_probe.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    futures_probe.add_argument(
        "--exchange", action="append", choices=tuple(item.value for item in FuturesExchange),
    )
    futures_spot_probe = subparsers.add_parser(
        "probe-futures-spot",
        help="Probe forming daily bars for explicitly selected active futures contracts",
    )
    futures_spot_probe.add_argument(
        "--exchange", action="append", required=True,
        choices=tuple(item.value for item in FuturesExchange),
    )
    futures_spot_probe.add_argument(
        "--contract", action="append", required=True,
        help="Tushare real-contract symbol, for example CU2609.SHF",
    )
    futures_spot_probe.add_argument(
        "--adapter", choices=("spot", "realtime"), default="spot",
    )
    futures_spot_probe.add_argument("--expected-trading-day", type=date.fromisoformat)
    futures_backfill = subparsers.add_parser(
        "backfill-futures",
        help="Resume canonical real-contract and roll-mapping futures history",
    )
    futures_backfill.add_argument("--start-date", type=date.fromisoformat)
    futures_backfill.add_argument(
        "--end-date", type=date.fromisoformat, default=date.today()
    )
    futures_backfill.add_argument(
        "--exchange", action="append",
        choices=tuple(item.value for item in FuturesExchange),
    )
    futures_backfill.add_argument("--max-contracts", type=int)
    futures_validate = subparsers.add_parser(
        "validate-futures",
        help="Compare stored final futures bars with exchange and public chart sources",
    )
    futures_validate.add_argument("--trade-date", type=date.fromisoformat, required=True)
    futures_validate.add_argument("--symbol", action="append", dest="symbols", required=True)
    futures_validate.add_argument(
        "--source", action="append", choices=("exchange", "sina"), dest="sources",
    )
    futures_validate.add_argument(
        "--output", type=Path, default=Path("data/reports/futures-validation.json")
    )
    futures_takeover = subparsers.add_parser(
        "audit-futures-takeover",
        help="Compare retained provisional futures observations with final rows",
    )
    futures_takeover.add_argument("--start-date", type=date.fromisoformat, required=True)
    futures_takeover.add_argument("--end-date", type=date.fromisoformat, required=True)
    futures_takeover.add_argument("--symbol", action="append", dest="symbols", required=True)
    futures_takeover.add_argument(
        "--output", type=Path,
        default=Path("data/reports/futures-provisional-takeover.json"),
    )

    backfill = subparsers.add_parser("backfill-stocks", help="Resume full-market stock daily backfill")
    backfill.add_argument("--years", type=int, default=30)
    backfill.add_argument("--start-date", type=date.fromisoformat)
    backfill.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    backfill.add_argument("--max-dates", type=int)
    backfill.add_argument("--progress-every", type=int, default=10)
    backfill.add_argument("--refresh-last-trading-days", type=int, default=0)
    universe = subparsers.add_parser(
        "backfill-universe", help="Resume configured ETF, index, or sector history"
    )
    universe.add_argument("--scope", choices=("etf", "index", "sector", "all"), required=True)
    universe.add_argument("--years", type=int, default=30)
    universe.add_argument("--start-date", type=date.fromisoformat)
    universe.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    universe.add_argument("--max-symbols", type=int)
    universe.add_argument("--refresh-lookback-days", type=int, default=0)
    catalog = subparsers.add_parser(
        "sync-catalog", help="Refresh the complete searchable ETF and board catalogs"
    )
    catalog.add_argument("--scope", choices=("etf", "dc", "ths", "all"), required=True)
    catalog.add_argument("--observed-on", type=date.fromisoformat, default=date.today())
    expanded = subparsers.add_parser(
        "backfill-expanded", help="Resume full ETF, Eastmoney-board, or THS-board history"
    )
    expanded.add_argument("--scope", choices=("etf", "dc", "ths", "all"), required=True)
    expanded.add_argument("--years", type=int, default=30)
    expanded.add_argument("--start-date", type=date.fromisoformat)
    expanded.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    expanded.add_argument("--max-symbols", type=int)
    expanded.add_argument("--refresh-lookback-days", type=int, default=0)
    members = subparsers.add_parser(
        "sync-board-members", help="Refresh current Eastmoney or THS board memberships"
    )
    members.add_argument("--source", choices=("dc", "ths"), required=True)
    members.add_argument("--observed-on", type=date.fromisoformat, default=date.today())
    members.add_argument("--max-symbols", type=int)
    expanded_report = subparsers.add_parser(
        "expanded-report", help="Write ETF and board catalog/history coverage"
    )
    expanded_report.add_argument(
        "--output", type=Path, default=Path("data/reports/expanded-coverage.json")
    )
    migrate = subparsers.add_parser(
        "migrate-data", help="Apply an idempotent documented data migration"
    )
    migrate.add_argument(
        "--migration", choices=("ths-volume-shares", "exclude-etf-links"), required=True
    )
    coverage = subparsers.add_parser(
        "coverage-report", help="Write per-kind and per-symbol daily-data coverage"
    )
    coverage.add_argument(
        "--output", type=Path, default=Path("data/reports/universe-coverage.json")
    )
    validate = subparsers.add_parser(
        "validate-date", help="Compare stored Tushare OHLCV with configured free providers"
    )
    validate.add_argument("--trade-date", type=date.fromisoformat, required=True)
    validate.add_argument("--symbol", action="append", dest="symbols")
    board_validate = subparsers.add_parser(
        "validate-board-date", help="Compare one stored DC or THS board with AkShare"
    )
    board_validate.add_argument("--source", choices=("dc", "ths"), required=True)
    board_validate.add_argument("--symbol", required=True)
    board_validate.add_argument("--trade-date", type=date.fromisoformat, required=True)
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
    if args.command == "probe-futures":
        if not settings.futures.enabled or not settings.futures.canonical.enabled:
            raise RuntimeError("futures canonical Provider is disabled")
        selected = tuple(
            FuturesExchange(value) for value in (args.exchange or settings.futures.exchanges)
        )
        provider = TushareFuturesProvider(settings.futures.canonical)
        summaries = []
        for exchange in selected:
            catalog = provider.discover_exchange(exchange, args.as_of)
            calendar = provider.calendar(exchange, args.as_of - timedelta(days=31), args.as_of)
            open_days = [item.calendar_date for item in calendar if item.is_open]
            active = [
                item for item in catalog.contracts
                if item.lifecycle_status is FuturesLifecycleStatus.TRADING
            ]
            daily_rows = 0
            latest_daily = None
            if active and open_days:
                bars = provider.fetch_contract_daily(
                    active[0], open_days[0], open_days[-1]
                )
                daily_rows = len(bars)
                latest_daily = bars[-1].trading_day if bars else None
            mapping_rows = 0
            if catalog.continuous_series and open_days:
                mappings = provider.fetch_roll_mappings(
                    catalog.continuous_series[0],
                    catalog.contracts,
                    open_days[0],
                    open_days[-1],
                )
                mapping_rows = len(mappings)
            summaries.append({
                "exchange": exchange.value,
                "products": len(catalog.products),
                "contracts": len(catalog.contracts),
                "active_contracts": len(active),
                "continuous_series": len(catalog.continuous_series),
                "calendar_rows": len(calendar),
                "open_days": len(open_days),
                "sample_daily_rows": daily_rows,
                "latest_sample_daily": latest_daily,
                "sample_mapping_rows": mapping_rows,
            })
        print(json.dumps({
            "provider": provider.code,
            "as_of": args.as_of,
            "exchanges": summaries,
        }, ensure_ascii=False, indent=2, default=str))
        return
    if args.command == "probe-futures-spot":
        if not settings.futures.enabled or not settings.futures.canonical.enabled:
            raise RuntimeError("futures canonical Provider is disabled")
        if not settings.futures.provisional.enabled:
            raise RuntimeError("futures provisional Provider is disabled")
        requested = tuple(dict.fromkeys(item.upper() for item in args.contract))
        if len(requested) > settings.futures.provisional.max_contracts:
            raise ValueError(
                "requested futures contracts exceed configured provisional maximum"
            )
        canonical = TushareFuturesProvider(settings.futures.canonical)
        discovered = {}
        product_display_names = {}
        for value in dict.fromkeys(args.exchange):
            catalog = canonical.discover_exchange(
                FuturesExchange(value), args.expected_trading_day or date.today()
            )
            product_display_names.update(
                (item.symbol, item.display_name) for item in catalog.products
            )
            discovered.update(
                (item.provider_symbol.upper(), item) for item in catalog.contracts
            )
        missing = [item for item in requested if item not in discovered]
        if missing:
            raise ValueError(f"undiscovered futures contracts: {', '.join(missing)}")
        selected = [discovered[item] for item in requested]
        inactive = [
            item.provider_symbol for item in selected
            if item.lifecycle_status is not FuturesLifecycleStatus.TRADING
        ]
        if inactive:
            raise ValueError(f"futures contracts are not trading: {', '.join(inactive)}")
        provisional = (
            AkShareFuturesSpotProvider(settings.futures.provisional)
            if args.adapter == "spot"
            else AkShareFuturesRealtimeProvider(
                settings.futures.provisional, product_display_names
            )
        )
        monitor = FuturesProviderMonitor(
            provisional, settings.futures.provisional.stale_after_seconds
        )
        bars = monitor.fetch(selected, args.expected_trading_day)
        payload = {
            "provider": provisional.code,
            "health": monitor.status(),
            "requested_contracts": requested,
            "missing_contracts": provisional.missing_contracts,
            "bars": [
                {
                    "symbol": item.symbol,
                    "trading_day": item.trading_day,
                    "provider_time": item.provider_time,
                    "ohlc": [item.open, item.high, item.low, item.close],
                    "volume_contracts": item.volume_contracts,
                    "open_interest_contracts": item.open_interest_contracts,
                    "previous_settlement_available": item.previous_settlement is not None,
                    "settlement_available": item.settlement is not None,
                    "amount_available": item.amount is not None,
                    "state": item.state.value,
                }
                for item in bars
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
        return
    if args.command == "backfill-futures":
        if not settings.futures.enabled or not settings.futures.canonical.enabled:
            raise RuntimeError("futures canonical Provider is disabled")
        selected = tuple(
            FuturesExchange(value)
            for value in (args.exchange or settings.futures.exchanges)
        )
        provider = TushareFuturesProvider(settings.futures.canonical)
        with _open_store(settings) as store:
            result = run_futures_backfill(
                provider,
                store,
                selected,
                args.start_date or settings.futures.history_start,
                args.end_date,
                as_of=args.end_date,
                max_contracts=args.max_contracts,
            )
            store.checkpoint("PASSIVE")
        print(json.dumps(asdict(result), ensure_ascii=False, default=str, indent=2))
        return
    if args.command == "validate-futures":
        providers = []
        for source in tuple(dict.fromkeys(args.sources or ("exchange", "sina"))):
            providers.append(
                AkShareExchangeFuturesValidationProvider()
                if source == "exchange"
                else AkShareSinaFuturesValidationProvider()
            )
        with _open_store(settings) as store:
            report = validate_futures_contracts(
                store, providers, args.symbols, args.trade_date,
                price_abs_tolerance=settings.validation.price_abs_tolerance,
            )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        counts = {
            status: sum(item.status == status for item in report.results)
            for status in ("match", "partial", "mismatch", "missing", "error")
        }
        print(
            f"futures_validation output={args.output} trade_date={args.trade_date} "
            f"checked={len(report.results)} "
            + " ".join(f"{key}={value}" for key, value in counts.items())
        )
        return
    if args.command == "audit-futures-takeover":
        with _open_store(settings) as store:
            report = compare_futures_provisional_takeovers(
                store, args.symbols, args.start_date, args.end_date,
                price_abs_tolerance=settings.validation.price_abs_tolerance,
            )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        counts = {
            status: sum(item.status == status for item in report.results)
            for status in (
                "match", "partial", "changed", "pending-final", "missing-catalog"
            )
        }
        print(
            f"futures_takeover_audit output={args.output} "
            f"start={args.start_date} end={args.end_date} rows={len(report.results)} "
            + " ".join(f"{key}={value}" for key, value in counts.items())
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
    if args.command == "validate-board-date":
        provider = TushareDailyProvider(settings.tushare)
        entries = _catalog_entries(provider, args.source, args.trade_date)
        descriptors = {
            entry.instrument.symbol: (entry.instrument.name, entry.category)
            for entry in entries
        }
        if args.symbol not in descriptors:
            raise ValueError(f"board symbol not found in {args.source} catalog: {args.symbol}")
        if args.source == "dc":
            primary_source = "tushare_dc"
            validator = AkShareEastmoneyBoardValidationProvider(descriptors)
        else:
            primary_source = "tushare_ths"
            validator = AkShareThsBoardValidationProvider(descriptors)
        with _open_store(settings) as store:
            summary = validate_symbols(
                store,
                primary_source,
                [validator],
                [args.symbol],
                args.trade_date,
                settings.validation.price_abs_tolerance,
                settings.validation.volume_rel_tolerance,
            )
        print(
            f"board_validation source={args.source} symbol={args.symbol} "
            f"trade_date={args.trade_date} checked={summary.checked} "
            f"matched={summary.matched} mismatched={summary.mismatched} "
            f"missing={summary.missing} errors={summary.errors}"
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
    if args.command == "backfill-universe":
        provider = TushareDailyProvider(settings.tushare)
        start_date = args.start_date or years_ago(args.end_date, args.years)
        selected_scopes = ("etf", "index", "sector") if args.scope == "all" else (args.scope,)
        with _open_store(settings) as store:
            for scope in selected_scopes:
                if scope == "etf":
                    instruments = provider.list_etfs(settings.universe.etfs)
                    kind = InstrumentKind.ETF
                elif scope == "index":
                    instruments = provider.list_broad_indices(settings.universe.broad_indices)
                    kind = InstrumentKind.INDEX
                else:
                    instruments = provider.list_sectors(
                        settings.universe.sector_source, settings.universe.sector_level
                    )
                    kind = InstrumentKind.SECTOR
                result = run_symbol_backfill(
                    provider,
                    store,
                    scope,
                    kind,
                    instruments,
                    start_date,
                    args.end_date,
                    max_symbols=args.max_symbols,
                    fallback_provider=(
                        AkShareSwValidationProvider() if kind is InstrumentKind.SECTOR else None
                    ),
                    force_refresh_from=(
                        args.end_date - timedelta(days=args.refresh_lookback_days)
                        if args.refresh_lookback_days > 0 else None
                    ),
                )
                print(
                    f"symbol_backfill_summary scope={scope} "
                    f"configured_symbols={result.configured_symbols} "
                    f"skipped_symbols={result.skipped_symbols} "
                    f"completed_symbols={result.completed_symbols} "
                    f"fetched_rows={result.fetched_rows} changed_rows={result.changed_rows}"
                )
            checkpoint = store.checkpoint("PASSIVE")
        print(
            f"sqlite_checkpoint busy={checkpoint[0]} log_pages={checkpoint[1]} "
            f"checkpointed_pages={checkpoint[2]}"
        )
        return
    if args.command == "sync-catalog":
        provider = TushareDailyProvider(settings.tushare)
        scopes = ("etf", "dc", "ths") if args.scope == "all" else (args.scope,)
        with _open_store(settings) as store:
            for scope in scopes:
                entries = _catalog_entries(provider, scope, args.observed_on)
                stored = store.upsert_catalog_entries(entries)
                print(
                    f"catalog_sync scope={scope} observed_on={args.observed_on} "
                    f"entries={stored}"
                )
        return
    if args.command == "backfill-expanded":
        provider = TushareDailyProvider(settings.tushare)
        start_date = args.start_date or years_ago(args.end_date, args.years)
        scopes = ("etf", "dc", "ths") if args.scope == "all" else (args.scope,)
        with _open_store(settings) as store:
            for scope in scopes:
                entries = _catalog_entries(provider, scope, args.end_date)
                store.upsert_catalog_entries(entries)
                eligible = [
                    entry.instrument
                    for entry in entries
                    if entry.listed_on is None or entry.listed_on <= args.end_date
                ]
                if scope == "etf":
                    daily_provider = provider
                    kind = InstrumentKind.ETF
                    sync_scope = "expanded_etf"
                else:
                    daily_provider = TushareBoardDailyProvider(
                        provider, "eastmoney" if scope == "dc" else "ths"
                    )
                    kind = InstrumentKind.SECTOR
                    sync_scope = f"{scope}_board"
                result = run_symbol_backfill(
                    daily_provider,
                    store,
                    sync_scope,
                    kind,
                    eligible,
                    start_date,
                    args.end_date,
                    max_symbols=args.max_symbols,
                    allow_empty_initial=True,
                    instrument_start_dates={
                        entry.instrument.symbol: entry.listed_on
                        for entry in entries
                        if entry.listed_on is not None
                    },
                    allow_unrepaired_rejections=scope in {"dc", "ths"},
                    force_refresh_from=(
                        args.end_date - timedelta(days=args.refresh_lookback_days)
                        if args.refresh_lookback_days > 0 else None
                    ),
                )
                remaining = (
                    result.configured_symbols
                    - result.skipped_symbols
                    - result.completed_symbols
                )
                print(
                    f"expanded_backfill_summary scope={scope} "
                    f"catalog_symbols={len(entries)} eligible_symbols={len(eligible)} "
                    f"skipped_symbols={result.skipped_symbols} "
                    f"completed_symbols={result.completed_symbols} remaining_symbols={remaining} "
                    f"fetched_rows={result.fetched_rows} changed_rows={result.changed_rows}"
                )
            checkpoint = store.checkpoint("PASSIVE")
        print(
            f"sqlite_checkpoint busy={checkpoint[0]} log_pages={checkpoint[1]} "
            f"checkpointed_pages={checkpoint[2]}"
        )
        return
    if args.command == "sync-board-members":
        provider = TushareDailyProvider(settings.tushare)
        source_system = "eastmoney" if args.source == "dc" else "ths"
        source = "tushare_dc" if args.source == "dc" else "tushare_ths"
        entries = _catalog_entries(provider, args.source, args.observed_on)
        selected = entries[: args.max_symbols] if args.max_symbols else entries
        memberships = 0
        with _open_store(settings) as store:
            store.upsert_catalog_entries(entries)
            for index, entry in enumerate(selected, start=1):
                rows = provider.list_board_members(
                    source_system, entry.instrument.symbol, entry.observed_on
                )
                memberships += store.replace_board_memberships(
                    source, entry.instrument.symbol, entry.observed_on, rows
                )
                logging.info(
                    "board_membership_progress source=%s completed=%d total=%d symbol=%s rows=%d",
                    source,
                    index,
                    len(selected),
                    entry.instrument.symbol,
                    len(rows),
                )
        print(
            f"board_membership_summary source={source} boards={len(selected)} "
            f"memberships={memberships}"
        )
        return
    if args.command == "expanded-report":
        with _open_store(settings) as store:
            rows = store.list_catalog_coverage_rows()
            incidents = [
                item
                for item in store.list_provider_incidents()
                if item.source in {"tushare", "tushare_dc", "tushare_ths"}
                and item.status == "open"
            ]
        summaries: dict[str, dict[str, object]] = {}
        for row in rows:
            key = str(row["catalog_source"])
            summary = summaries.setdefault(
                key,
                {"instruments": 0, "instruments_with_history": 0, "rows": 0},
            )
            summary["instruments"] = int(summary["instruments"]) + 1
            summary["rows"] = int(summary["rows"]) + int(row["rows"])
            if int(row["rows"]) > 0:
                summary["instruments_with_history"] = (
                    int(summary["instruments_with_history"]) + 1
                )
        payload = {
            "generated_on": date.today(),
            "summaries": summaries,
            "open_incidents": [
                {
                    "source": item.source,
                    "scope": item.scope,
                    "trade_date": item.trade_date,
                    "type": item.incident_type,
                    "message": item.message,
                }
                for item in incidents
            ],
            "symbols": rows,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        print(
            f"expanded_report output={args.output} symbols={len(rows)} "
            f"open_incidents={len(incidents)}"
        )
        return
    if args.command == "migrate-data":
        with _open_store(settings) as store:
            if args.migration == "ths-volume-shares":
                affected = store.apply_volume_scale_migration(
                    "2026-08-02-ths-volume-shares", "tushare_ths", 100
                )
            elif args.migration == "exclude-etf-links":
                affected = store.apply_catalog_name_exclusion_migration(
                    "2026-08-02-exclude-etf-links",
                    "tushare",
                    "exchange_traded_equity_fund",
                    "\u8054\u63a5",
                )
            else:
                raise ValueError(f"unsupported data migration: {args.migration}")
            checkpoint = store.checkpoint("PASSIVE")
        print(
            f"data_migration migration={args.migration} affected_rows={affected} "
            f"checkpointed_pages={checkpoint[2]}"
        )
        return
    if args.command == "coverage-report":
        kinds = {InstrumentKind.ETF, InstrumentKind.INDEX, InstrumentKind.SECTOR}
        with _open_store(settings) as store:
            rows = store.list_instrument_coverage(kinds)
        summaries = {}
        for kind in sorted(kinds, key=lambda item: item.value):
            selected = [row for row in rows if row.kind is kind]
            summaries[kind.value] = {
                "instruments": len(selected),
                "rows": sum(row.row_count for row in selected),
                "first_trade_date": min(
                    (row.first_trade_date for row in selected if row.first_trade_date),
                    default=None,
                ),
                "last_trade_date": max(
                    (row.last_trade_date for row in selected if row.last_trade_date),
                    default=None,
                ),
            }
        payload = {
            "generated_on": date.today(),
            "summaries": summaries,
            "symbols": [
                {
                    "symbol": row.symbol,
                    "name": row.name,
                    "kind": row.kind.value,
                    "active": row.active,
                    "first_trade_date": row.first_trade_date,
                    "last_trade_date": row.last_trade_date,
                    "rows": row.row_count,
                    "sources": dict(row.source_rows),
                }
                for row in rows
            ],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"coverage_report output={args.output} symbols={len(rows)}")
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
            refresh_last_trading_days=args.refresh_last_trading_days,
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
