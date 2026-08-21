from datetime import date, datetime, timedelta, timezone
import json

from stock_harness.analysis_inputs import AnalysisHorizons, AnalysisTimeframe
from stock_harness.models import (
    FuturesBarState,
    FuturesCalendarDay,
    FuturesContinuousSeries,
    FuturesContract,
    FuturesDailyBar,
    FuturesExchange,
    FuturesLifecycleStatus,
    FuturesPriceBasis,
    FuturesProduct,
    FuturesRollMapping,
    FuturesSeriesKind,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.trend_analysis import TrendAnalysisService
from stock_harness.trend_replay import compare_replays, replay_trend_analysis


HORIZONS = AnalysisHorizons(6, 12, 24)


def _fixture(
    *, mutate_after: date | None = None
) -> tuple[
    SQLiteMarketDataStore,
    FuturesContract,
    FuturesContract,
    FuturesContinuousSeries,
    list[date],
]:
    store = SQLiteMarketDataStore(":memory:")
    start = date(2026, 6, 1)
    days = [start + timedelta(days=index) for index in range(40)]
    product = FuturesProduct(
        "FUTPROD:SHFE:CU", "CU", "Copper", FuturesExchange.SHFE,
        None, 5, "contract", "CNY/tonne",
    )
    front = FuturesContract(
        "FUT:SHFE:CU:202606", "CU2606.SHF", product.symbol, "Copper 2606",
        FuturesExchange.SHFE, "202606", days[0], days[18], days[21],
        None, 5, "contract", "CNY/tonne", FuturesLifecycleStatus.EXPIRED,
    )
    next_contract = FuturesContract(
        "FUT:SHFE:CU:202607", "CU2607.SHF", product.symbol, "Copper 2607",
        FuturesExchange.SHFE, "202607", days[8], days[-1] + timedelta(days=20),
        days[-1] + timedelta(days=23), None, 5, "contract", "CNY/tonne",
        FuturesLifecycleStatus.TRADING,
    )
    series = FuturesContinuousSeries(
        "FUTCONT:SHFE:CU:MAIN:raw", "CU.SHF", product.symbol, "Copper main",
        FuturesExchange.SHFE, FuturesSeriesKind.MAIN, "MAIN",
        FuturesPriceBasis.RAW, "mapping-v1",
    )
    store.upsert_futures_catalog(
        "tushare-futures", [product], [front, next_contract], [series]
    )
    store.upsert_futures_calendar("tushare-futures", [
        FuturesCalendarDay(
            FuturesExchange.SHFE, day, True, days[index - 1] if index else None
        )
        for index, day in enumerate(days)
    ])
    store.upsert_futures_roll_mappings("tushare-futures", [
        FuturesRollMapping(
            series.symbol, series.provider_symbol, days[0],
            front.symbol, front.provider_symbol,
        ),
        FuturesRollMapping(
            series.symbol, series.provider_symbol, days[18],
            next_contract.symbol, next_contract.provider_symbol,
        ),
    ])

    rows: list[FuturesDailyBar] = []
    for index, day in enumerate(days):
        close = 100 + (index % 8 if (index // 8) % 2 == 0 else 7 - index % 8)
        if index == 26:
            open_price = high = low = close = 118
        else:
            open_price, high, low = close - 0.5, close + 1, close - 1
        if mutate_after is not None and day > mutate_after:
            open_price, high, low, close = 500 + index, 502 + index, 499 + index, 501 + index
        mapped = front if index < 18 else next_contract
        provider_date = day - timedelta(days=1) if index in {12, 24} else day
        if index != 22:
            rows.append(FuturesDailyBar(
                series.symbol, day, provider_date, open_price, high, low, close,
                close - 1, close, close - 1, 1_000 + index * 10,
                1_000_000 + index * 1_000, 8_000 + index * 25,
                (-300 if index == 18 else 25), None, "tushare-futures",
                FuturesBarState.FINAL, mapped_contract_symbol=mapped.symbol,
                roll_event=index == 18,
            ))
        if index <= 18:
            rows.append(FuturesDailyBar(
                front.symbol, day, provider_date, open_price, high, low, close,
                close - 1, close, close - 1, 700 + index, 700_000 + index,
                5_000 - index * 50, -50, None, "tushare-futures",
                FuturesBarState.FINAL,
            ))
        if index >= 8:
            rows.append(FuturesDailyBar(
                next_contract.symbol, day, provider_date, open_price, high, low, close,
                close - 1, close, close - 1, 500 + index, 500_000 + index,
                2_000 + index * 80, 80, None, "tushare-futures",
                FuturesBarState.FINAL,
            ))
    store.upsert_futures_daily_bars("tushare-futures", rows)
    return store, front, next_contract, series, days


def _item_payload(snapshot, item_id: str) -> dict[str, object]:
    item = next(item for item in snapshot.items if item.item_id == item_id)
    return json.loads(item.payload_json)


def test_futures_replay_is_causal_across_night_expiry_roll_limit_and_missing_session():
    baseline, front, _, series, days = _fixture()
    cutoff = days[27]
    mutated, _, _, _, _ = _fixture(mutate_after=cutoff)
    cutoffs = [days[12], days[17], days[23], cutoff]
    try:
        baseline_replay = replay_trend_analysis(
            TrendAnalysisService(baseline), series.symbol, cutoffs,
            horizons=HORIZONS, config_version="futures-replay-v1",
        )
        mutated_replay = replay_trend_analysis(
            TrendAnalysisService(mutated), series.symbol, cutoffs,
            horizons=HORIZONS, config_version="futures-replay-v1",
        )

        assert compare_replays(baseline_replay, mutated_replay) == ()
        assert [item.items for item in baseline_replay] == [
            item.items for item in mutated_replay
        ]
        assert baseline.list_futures_daily_bars(
            series.symbol, days[12], days[12]
        )[0].provider_date == days[11]
        roll = _item_payload(
            baseline_replay[-1], "futures-roll-qualification"
        )
        assert roll["latest_roll_date"] == days[18].isoformat()
        assert roll["eligible_start_date"] == days[18].isoformat()
        assert days[22].isoformat() in baseline_replay[-1].warnings_json
        assert baseline_replay[-1].items

        expired = replay_trend_analysis(
            TrendAnalysisService(baseline), front.symbol, [days[25]],
            horizons=HORIZONS, config_version="expired-contract-v1",
        )[0]
        assert "unexplained_missing_bars" not in expired.warnings_json
    finally:
        baseline.close()
        mutated.close()


def test_futures_replay_fixture_revises_provisional_extreme_after_takeover():
    store, _, next_contract, series, days = _fixture()
    current = days[-1] + timedelta(days=1)
    observed = datetime(2026, 7, 11, 6, 30, tzinfo=timezone.utc)
    try:
        store.upsert_futures_calendar("tushare-futures", [
            FuturesCalendarDay(FuturesExchange.SHFE, current, True, days[-1])
        ])
        provisional = FuturesDailyBar(
            next_contract.symbol, current, current - timedelta(days=1),
            130, 130, 130, 130, 110, None, 109, 2_000, None, 10_000, None,
            None, "akshare-futures-zh-spot", FuturesBarState.PROVISIONAL,
            provider_time=observed,
        )
        store.upsert_futures_provisional_daily_bars(
            provisional.source, [provisional], observed
        )
        service = TrendAnalysisService(store)
        preview = service.recalculate(
            series.symbol, [AnalysisTimeframe.DAILY], HORIZONS,
            config_version="futures-reversal-v1", include_preview=True,
            as_of_date=current,
        )[0]
        preview_summary = next(
            item["payload"] for item in preview["items"]
            if item["item_id"] == "key-level-volume-profile-evidence"
        )
        assert preview_summary["latest_close"] == 130

        canonical_rows = [
            FuturesDailyBar(
                symbol, current, current, 90, 90, 90, 90, 110, 90, 109,
                1_500, 900_000, 9_500, -500, None, "tushare-futures",
                FuturesBarState.FINAL,
                mapped_contract_symbol=(
                    next_contract.symbol if symbol == series.symbol else None
                ),
            )
            for symbol in (next_contract.symbol, series.symbol)
        ]
        store.upsert_futures_daily_bars("tushare-futures", canonical_rows)
        official = service.recalculate(
            series.symbol, [AnalysisTimeframe.DAILY], HORIZONS,
            config_version="futures-reversal-v1", include_preview=True,
            as_of_date=current,
        )[0]
        official_summary = next(
            item["payload"] for item in official["items"]
            if item["item_id"] == "key-level-volume-profile-evidence"
        )

        assert official_summary["latest_close"] == 90
        assert official["source_observed_at_ms"] is None
        assert official["run_id"] != preview["run_id"]
        assert official["input_digest"] != preview["input_digest"]
    finally:
        store.close()


def test_futures_analysis_remains_opt_in_and_only_registered_target_is_queued():
    store, front, next_contract, series, days = _fixture()
    try:
        assert store.claim_generated_analysis_targets() == []
        service = TrendAnalysisService(store)
        service.recalculate(
            series.symbol, [AnalysisTimeframe.DAILY], HORIZONS,
            config_version="futures-opt-in-v1", include_preview=False,
            as_of_date=days[-1],
        )
        assert store.claim_generated_analysis_targets() == []

        next_day = days[-1] + timedelta(days=1)
        store.upsert_futures_daily_bars("tushare-futures", [
            FuturesDailyBar(
                symbol, next_day, next_day, 110, 112, 109, 111,
                109, 110, 109, 2_000, 1_000_000, 12_000, 100,
                None, "tushare-futures", FuturesBarState.FINAL,
                mapped_contract_symbol=(
                    next_contract.symbol if symbol == series.symbol else None
                ),
            )
            for symbol in (front.symbol, next_contract.symbol, series.symbol)
        ])

        claims = store.claim_generated_analysis_targets()
        assert len(claims) == 1
        assert claims[0].symbol == series.symbol
        assert claims[0].timeframe == AnalysisTimeframe.DAILY.value
        assert claims[0].dirty_from == next_day
        assert claims[0].dirty_through == next_day
        assert claims[0].reason == "futures_canonical_bar_changed"
    finally:
        store.close()
