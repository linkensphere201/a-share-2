from datetime import date, datetime, timedelta, timezone
import json

import pytest

from stock_harness.analysis_inputs import AnalysisHorizons, AnalysisTimeframe
from stock_harness.models import (
    AdjustmentFactor,
    DailyBar,
    Instrument,
    InstrumentKind,
    ProvisionalDailyBar,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.trend_analysis import TrendAnalysisService
from stock_harness.trend_replay import (
    compare_replays,
    replay_trend_analysis,
    summarize_replay,
)


HORIZONS = AnalysisHorizons(6, 12, 24)


def _replay_store(
    *,
    mutate_after: date | None = None,
    canonical_bar_count: int | None = None,
) -> tuple[SQLiteMarketDataStore, list[date]]:
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("000001.SZ", "Ping An Bank", InstrumentKind.STOCK, "SZ")
    ])
    start = date(2026, 6, 1)
    days = [start + timedelta(days=index) for index in range(36)]
    closes = [
        10 + (index % 8 if (index // 8) % 2 == 0 else 7 - index % 8) * 0.35
        for index in range(len(days))
    ]
    if mutate_after is not None:
        closes = [
            close if day <= mutate_after else 30 + index * 1.7
            for index, (day, close) in enumerate(zip(days, closes))
        ]
    bars = [
        DailyBar(
            "000001.SZ", day, close - 0.1, close + 0.25, close - 0.3,
            close, 100 + index * 5,
        )
        for index, (day, close) in enumerate(zip(days, closes))
    ]
    selected_bars = bars[:canonical_bar_count]
    selected_days = days[:canonical_bar_count]
    store.upsert_daily_bars("tushare", selected_bars)
    store.upsert_adjustment_factors("tushare", [
        AdjustmentFactor("000001.SZ", day, 1) for day in selected_days
    ])
    store.upsert_trading_dates("tushare", selected_days)
    return store, days


def test_replay_captures_complete_production_outputs_in_chronological_order():
    store, days = _replay_store()
    try:
        snapshots = replay_trend_analysis(
            TrendAnalysisService(store), "000001.SZ",
            [days[17], days[23], days[29]], horizons=HORIZONS,
        )

        assert [item.as_of_date for item in snapshots] == [days[17], days[23], days[29]]
        assert all(len(item.input_digest) == 64 for item in snapshots)
        assert all(len(item.output_digest) == 64 for item in snapshots)
        assert all(dict(item.item_counts)["anchor"] > 0 for item in snapshots)
        assert all(item.items for item in snapshots)
        assert len({item.output_digest for item in snapshots}) == len(snapshots)
    finally:
        store.close()


def test_future_suffix_mutation_cannot_change_any_historical_production_output():
    baseline_store, days = _replay_store()
    cutoff = days[27]
    mutated_store, _ = _replay_store(mutate_after=cutoff)
    cutoffs = [days[17], days[22], cutoff]
    try:
        baseline = replay_trend_analysis(
            TrendAnalysisService(baseline_store), "000001.SZ", cutoffs,
            horizons=HORIZONS,
        )
        mutated = replay_trend_analysis(
            TrendAnalysisService(mutated_store), "000001.SZ", cutoffs,
            horizons=HORIZONS,
        )

        assert compare_replays(baseline, mutated) == ()
        assert [item.items for item in baseline] == [item.items for item in mutated]
    finally:
        baseline_store.close()
        mutated_store.close()


def test_replay_records_structural_revisions_before_each_next_bar_is_revealed():
    store, days = _replay_store()
    try:
        snapshots = replay_trend_analysis(
            TrendAnalysisService(store), "000001.SZ", days[17:34],
            horizons=HORIZONS,
        )
        events_by_date = {
            snapshot.as_of_date: set(snapshot.structural_events)
            for snapshot in snapshots
        }

        assert "retest" in events_by_date[days[17]]
        assert "false-breakout-risk" in events_by_date[days[18]]
        assert "upward-breakout" in events_by_date[days[22]]
        assert "retest" in events_by_date[days[25]]
        assert "downward-breakdown" in events_by_date[days[30]]
        assert "retest" in events_by_date[days[33]]
    finally:
        store.close()


def test_incremental_replay_matches_a_fresh_full_run_at_the_same_cutoff():
    incremental_store, days = _replay_store()
    full_store, _ = _replay_store()
    try:
        incremental = replay_trend_analysis(
            TrendAnalysisService(incremental_store), "000001.SZ",
            [days[17], days[22], days[27], days[32]], horizons=HORIZONS,
        )
        full = replay_trend_analysis(
            TrendAnalysisService(full_store), "000001.SZ", [days[32]],
            horizons=HORIZONS,
        )

        assert compare_replays(incremental[-1:], full) == ()
        assert incremental[-1].items == full[0].items
    finally:
        incremental_store.close()
        full_store.close()


def test_replay_retains_a_confirmed_pattern_when_the_next_bar_invalidates_it():
    store, days = _replay_store()
    invalidation_day = days[26]
    store.upsert_daily_bars("tushare", [
        DailyBar(
            "000001.SZ", invalidation_day, 9, 9.2, 8.8, 9, 500
        )
    ])
    try:
        before, after = replay_trend_analysis(
            TrendAnalysisService(store), "000001.SZ",
            [days[25], invalidation_day], horizons=HORIZONS,
        )

        before_patterns = [
            json.loads(item.payload_json) for item in before.items
            if item.item_type == "pattern"
        ]
        after_patterns = [
            json.loads(item.payload_json) for item in after.items
            if item.item_type == "pattern"
        ]
        assert any(
            item["pattern_type"] == "v-bottom"
            and item["completion_state"] == "confirmed"
            for item in before_patterns
        )
        invalidated = next(
            item for item in after_patterns
            if item["pattern_type"] == "v-bottom"
            and item["completion_state"] == "invalidated"
        )
        assert invalidated["invalidation_date"] == invalidation_day.isoformat()
        assert invalidated["primary"] is False
    finally:
        store.close()


def test_replay_metrics_separate_recognition_lag_from_event_turnover():
    store, days = _replay_store()
    try:
        snapshots = replay_trend_analysis(
            TrendAnalysisService(store), "000001.SZ", days[17:34],
            horizons=HORIZONS,
        )
        metrics = summarize_replay(snapshots)

        assert metrics.snapshot_count == 17
        assert metrics.unique_anchor_count > 0
        assert metrics.anchor_confirmation_lag_mean_days is not None
        assert metrics.anchor_confirmation_lag_mean_days > 0
        assert metrics.anchor_confirmation_lag_max_days is not None
        assert metrics.unique_pattern_count > 0
        assert metrics.pattern_availability_lag_mean_days is not None
        assert 0 <= metrics.candidate_stability_mean <= 1
        assert 0 < metrics.alert_turnover_rate <= 1
        assert dict(metrics.event_counts)["upward-breakout"] >= 1
        assert dict(metrics.event_counts)["downward-breakdown"] >= 1
    finally:
        store.close()


def test_replay_metrics_require_a_snapshot():
    with pytest.raises(ValueError, match="at least one snapshot"):
        summarize_replay([])


@pytest.mark.parametrize(("bar_index", "expected_event"), [
    (22, "upward-breakout"),
    (30, "downward-breakdown"),
])
def test_provisional_structural_event_becomes_official_after_canonical_takeover(
    bar_index: int,
    expected_event: str,
):
    store, days = _replay_store(canonical_bar_count=bar_index)
    trade_date = days[bar_index]
    close = 10 + (
        bar_index % 8 if (bar_index // 8) % 2 == 0 else 7 - bar_index % 8
    ) * 0.35
    observed = datetime(2026, 8, 18, 6, 30, tzinfo=timezone.utc)
    provisional = ProvisionalDailyBar(
        "000001.SZ", trade_date, close - 0.1, close + 0.25, close - 0.3,
        close, 100 + bar_index * 5, 10_000, close, 0,
        "eastmoney_selected", observed, observed,
    )
    store.upsert_provisional_daily_bars([provisional])
    try:
        service = TrendAnalysisService(store)
        preview = service.recalculate(
            "000001.SZ", [AnalysisTimeframe.DAILY], HORIZONS,
            config_version="preview-takeover", include_preview=True,
            as_of_date=trade_date,
        )[0]
        preview_events = [
            item["payload"] for item in preview["items"]
            if item["item_type"] == "transition"
            and item["payload"].get("event_kind") == expected_event
        ]
        assert preview["source_observed_at_ms"] == int(observed.timestamp() * 1000)
        assert preview_events and all(item["preview"] is True for item in preview_events)

        store.upsert_trading_dates("tushare", [trade_date])
        store.upsert_adjustment_factors("tushare", [
            AdjustmentFactor("000001.SZ", trade_date, 1)
        ])
        store.upsert_daily_bars("tushare", [
            DailyBar(
                "000001.SZ", trade_date, close - 0.1, close + 0.25,
                close - 0.3, close, 100 + bar_index * 5,
            )
        ])
        official = service.recalculate(
            "000001.SZ", [AnalysisTimeframe.DAILY], HORIZONS,
            config_version="preview-takeover", include_preview=True,
            as_of_date=trade_date,
        )[0]
        official_events = [
            item["payload"] for item in official["items"]
            if item["item_type"] == "transition"
            and item["payload"].get("event_kind") == expected_event
        ]

        assert official["source_observed_at_ms"] is None
        assert official_events and all(item["preview"] is False for item in official_events)
        assert official["run_id"] != preview["run_id"]
    finally:
        store.close()


def test_replay_rejects_empty_or_non_chronological_cutoffs():
    store, days = _replay_store()
    try:
        service = TrendAnalysisService(store)
        with pytest.raises(ValueError, match="at least one cutoff"):
            replay_trend_analysis(service, "000001.SZ", [], horizons=HORIZONS)
        with pytest.raises(ValueError, match="strictly chronological"):
            replay_trend_analysis(
                service, "000001.SZ", [days[20], days[19]], horizons=HORIZONS
            )
    finally:
        store.close()


def test_replay_comparison_rejects_different_cutoff_sets():
    store, days = _replay_store()
    try:
        service = TrendAnalysisService(store)
        first = replay_trend_analysis(service, "000001.SZ", [days[20]], horizons=HORIZONS)
        second = replay_trend_analysis(service, "000001.SZ", [days[21]], horizons=HORIZONS)
        with pytest.raises(ValueError, match="must match"):
            compare_replays(first, second)
    finally:
        store.close()
