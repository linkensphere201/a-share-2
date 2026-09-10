from __future__ import annotations

from datetime import date, timedelta

from stock_harness.models import (
    AdjustmentFactor,
    BoardMembership,
    DailyBar,
    Instrument,
    InstrumentKind,
    StoredDailyBar,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.stock_observation_scan import (
    build_board_member_scan_snapshot,
    build_unified_stock_pool_snapshot,
    scan_full_market_independent_strength,
)
from stock_harness.stock_relative_strength import (
    analyze_relative_strength,
    build_reference_context,
    classify_relative_strength,
    combine_reference_contexts,
    score_relative_strength,
)


def test_relative_strength_analysis_is_causal_and_detects_persistent_advance() -> None:
    market = _bars("000001.SH", [100 * 1.0002**index for index in range(150)])
    stock = _bars("000001.SZ", [10 * 1.0055**index for index in range(150)])
    cutoff = stock[139].trade_date
    baseline = analyze_relative_strength(
        "000001.SZ", stock, cutoff, market_references={"market": market},
    )
    mutated_future = [*stock[:140], *_bars(
        "000001.SZ", [500, 5, 800], start=stock[140].trade_date,
    )]
    replay = analyze_relative_strength(
        "000001.SZ", mutated_future, cutoff, market_references={"market": market},
    )

    assert baseline == replay
    assert baseline["classification"] == "independent-advance"
    assert baseline["eligible"] is True


def test_precomputed_reference_context_preserves_analysis_result() -> None:
    stock = _bars("000001.SZ", [10 * 1.003**index for index in range(140)])
    market = {"000001.SH": _bars(
        "000001.SH", [100 * 1.001**index for index in range(140)],
    )}
    board = {"BK001.DC": _bars(
        "BK001.DC", [1000 * 1.0015**index for index in range(140)],
    )}
    effective = stock[-1].trade_date

    direct = analyze_relative_strength(
        "000001.SZ", stock, effective,
        market_references=market, board_references=board,
    )
    cached = analyze_relative_strength(
        "000001.SZ", stock, effective,
        market_references=market, board_references=board,
        market_context=build_reference_context(market, effective),
        board_context=combine_reference_contexts([
            build_reference_context(board, effective),
        ]),
    )

    assert cached == direct


def test_relative_strength_classifies_resilience_decline_event_and_lifecycle() -> None:
    common = {
        "market_excess": {"20": .08}, "board_excess": {"20": .07},
        "returns": {"5": .04}, "residual_positive_ratio10": .7,
        "volume_ratio20": 1.2, "trend_state": "up",
        "market_correlation20": .2, "board_correlation20": .3,
    }
    assert classify_relative_strength({
        **common, "adjusted_residual": {"5": .04, "20": .1},
    }) == "independent-advance"
    assert classify_relative_strength({
        **common, "returns": {"5": 0.0, "20": 0.0},
        "market_excess": {"20": .07}, "board_excess": {"20": .07},
        "adjusted_residual": {"5": .02, "20": .08},
    }) == "counter-trend-resilience"
    assert classify_relative_strength({
        **common, "market_excess": {"20": -.1}, "board_excess": {"20": -.08},
        "adjusted_residual": {"5": -.04, "20": -.12}, "trend_state": "down",
    }) == "independent-decline"
    assert classify_relative_strength({
        **common, "adjusted_residual": {"1": .06, "5": .06, "20": .07},
        "residual_positive_ratio10": .4, "volume_ratio20": 3.0,
    }) == "one-session-event-anomaly"
    assert classify_relative_strength({
        **common, "adjusted_residual": {"5": -.01, "20": .1},
    }) == "decaying-independent-move"
    assert classify_relative_strength({
        **common, "adjusted_residual": {"5": .005, "20": .01},
        "market_correlation20": .8, "board_correlation20": .4,
    }) == "resynchronized"


def test_low_correlation_alone_never_qualifies_and_scores_are_cross_sectional() -> None:
    weak = _record("WEAK", "neutral", residual20=0, persistence=.3, trend="down")
    strong = _record(
        "STRONG", "independent-advance", residual20=.15,
        persistence=.8, trend="up",
    )
    scored = score_relative_strength([weak, strong])

    assert scored[0]["symbol"] == "STRONG"
    assert scored[0]["eligible"] is True
    assert next(item for item in scored if item["symbol"] == "WEAK")["eligible"] is False
    assert "correlation" not in scored[0]["score_components"]


def test_board_member_scan_reads_every_member_and_preserves_sources() -> None:
    store = SQLiteMarketDataStore(":memory:")
    effective = date(2026, 5, 20)
    instruments = [
        Instrument("000001.SH", "Market", InstrumentKind.INDEX, "SH"),
        Instrument("BK001.DC", "Board", InstrumentKind.SECTOR, "DC"),
        *[
            Instrument(f"00000{index}.SZ", f"Stock {index}", InstrumentKind.STOCK, "SZ")
            for index in range(1, 5)
        ],
    ]
    store.upsert_instruments(instruments)
    members = [
        BoardMembership("BK001.DC", item.symbol, item.name, "test", effective)
        for item in instruments if item.kind is InstrumentKind.STOCK
    ]
    store.replace_board_memberships("test", "BK001.DC", effective, members)
    daily: list[DailyBar] = []
    for instrument in instruments:
        rate = .0002
        if instrument.symbol == "000001.SZ":
            rate = .0055
        closes = [100 * (1 + rate) ** index for index in range(140)]
        daily.extend(DailyBar(
            instrument.symbol, effective - timedelta(days=139 - index),
            close, close * 1.01, close * .99, close,
            1_000_000 + index * 1000,
        ) for index, close in enumerate(closes))
    store.upsert_daily_bars("test", daily)
    board_pool = {"items": [{
        "symbol": "BK001.DC", "rank": 1,
        "sources": [{
            "source_type": "recognition-assignment",
            "source_reference": "weekly-run", "source_entity_key": "recent:000001.SZ",
            "reason": "recent-rank-1",
            "payload": {"member_symbol": "000001.SZ", "recognition_role": "recent-rank-1"},
        }],
    }]}

    snapshot = build_board_member_scan_snapshot(
        store, "daily-run", effective, board_pool,
    )

    assert snapshot["summary"]["item_count"] == 4
    assert snapshot["summary"]["recognized_count"] == 1
    leader = next(item for item in snapshot["items"] if item["symbol"] == "000001.SZ")
    assert leader["payload"]["classification"] == "independent-advance"
    assert {source["source_type"] for source in leader["sources"]} == {
        "board-membership", "recognition-assignment",
    }
    assert len(store.list_stock_board_memberships_many(["000001.SZ"])["000001.SZ"]) == 1
    all_memberships = store.list_all_stock_board_memberships()
    assert all_memberships["000001.SZ"] == store.list_stock_board_memberships_many(
        ["000001.SZ"]
    )["000001.SZ"]
    recent = store.get_recent_daily_bars_many(["000001.SZ"], effective, 3)
    assert len(recent["000001.SZ"]) == 3
    assert [bar.trade_date for bar in recent["000001.SZ"]] == sorted(
        bar.trade_date for bar in recent["000001.SZ"]
    )
    assert recent["000001.SZ"][-1].trade_date == effective
    market_scan = scan_full_market_independent_strength(store, effective)
    assert len(market_scan) == 4
    assert market_scan[0]["symbol"] == "000001.SZ"
    assert market_scan[0]["classification"] == "independent-advance"
    store.close()


def test_daily_bar_range_bulk_read_preserves_symbol_and_date_order() -> None:
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([
        Instrument("000001.SZ", "First", InstrumentKind.STOCK, "SZ"),
        Instrument("000002.SZ", "Second", InstrumentKind.STOCK, "SZ"),
    ])
    dates = [date(2026, 1, 2) + timedelta(days=index) for index in range(4)]
    store.upsert_daily_bars("test", [
        DailyBar(symbol, day, value, value, value, value, 100)
        for symbol, base in (("000001.SZ", 10), ("000002.SZ", 20))
        for index, day in enumerate(dates)
        for value in [base + index]
    ])

    result = store.get_daily_bars_range_many(
        ["000002.SZ", "000001.SZ"], dates[1], dates[3],
    )

    assert list(result) == ["000002.SZ", "000001.SZ"]
    assert [bar.trade_date for bar in result["000001.SZ"]] == dates[1:]
    assert [bar.close for bar in result["000002.SZ"]] == [21, 22, 23]
    store.close()


def test_unified_stock_pool_deduplicates_symbols_without_losing_sources() -> None:
    effective = date(2026, 9, 9)
    member = {
        "items": [{
            "symbol": "000001.SZ", "payload": {
                **_record(
                    "000001.SZ", "independent-advance", residual20=.12,
                    persistence=.8, trend="up",
                ),
                "score": 78, "recognized": True,
            },
            "sources": [{
                "source_type": "board-membership", "source_reference": "daily-run",
                "source_entity_key": "BK001.DC", "reason": "pooled-board-member",
                "payload": {},
            }],
        }],
    }
    independent = [{
        **_record(
            "000001.SZ", "independent-advance", residual20=.14,
            persistence=.9, trend="up",
        ),
        "score": 86, "rank": 1, "eligible": True,
    }]
    screener_run = {
        "run_id": "screen-run", "strategy_id": "major-line",
        "strategy_version": "v3", "as_of_date": effective,
    }
    screener = [{
        "symbol": "000001.SZ", "state": "retest", "rank": 1, "score": 88,
        "analysis_run_id": "m4-run", "line_item_id": "L1",
    }]
    manual = [{
        "signal_id": "stock-observation-pool", "symbol": "000002.SZ",
        "manual_pinned": True, "first_observed_date": effective,
        "last_observed_date": effective, "reasons": ["manual"],
    }]

    snapshot = build_unified_stock_pool_snapshot(
        "daily-run", effective, member, independent,
        screener_run=screener_run, screener_candidates=screener,
        manual_attention=manual,
    )

    assert [item["symbol"] for item in snapshot["items"]] == [
        "000002.SZ", "000001.SZ",
    ]
    merged = snapshot["items"][1]
    assert merged["payload"]["independent_score"] == 86
    assert merged["payload"]["source_types"] == [
        "board-membership", "independent-strength", "screener-result",
    ]
    assert {source["source_reference"] for source in merged["sources"]} == {
        "daily-run", "screen-run",
    }
    assert snapshot["items"][0]["lifecycle_state"] == "manual-pinned"
    merged["payload"]["presentation_bucket"] = "focus"
    merged["payload"]["focus_streak_sessions"] = 4
    prior = {**snapshot, "source_run_id": "daily-run"}
    carried = build_unified_stock_pool_snapshot(
        "next-run", effective + timedelta(days=1), {"items": []}, independent,
        prior_snapshot=prior,
    )
    assert carried["items"][0]["payload"]["previous_presentation_bucket"] == "focus"
    assert carried["items"][0]["payload"]["focus_streak_sessions"] == 5
    cooling = build_unified_stock_pool_snapshot(
        "next-run", effective + timedelta(days=1), {"items": []}, [],
        prior_snapshot=prior,
    )
    assert cooling["summary"]["cooldown_count"] == 2
    assert all(item["lifecycle_state"] == "cooldown" for item in cooling["items"])
    assert cooling["items"][0]["sources"][0]["source_reference"] == "daily-run"


def test_latest_screener_source_is_selected_by_effective_date() -> None:
    store = SQLiteMarketDataStore(":memory:")
    later = store.create_screener_run("major-line", "v3", date(2026, 9, 9), {})
    store.complete_screener_run(str(later["run_id"]), [])
    earlier = store.create_screener_run("major-line", "v3", date(2026, 9, 8), {})
    store.complete_screener_run(str(earlier["run_id"]), [])

    selected = store.get_latest_succeeded_screener_run(date(2026, 9, 8))

    assert selected is not None
    assert selected["run_id"] == earlier["run_id"]
    store.close()


def test_unified_pool_preserves_prior_focus_with_current_safe_scan_record() -> None:
    prior = {
        "source_run_id": "prior-run",
        "items": [{
            "symbol": "000001.SZ", "rank": 1, "lifecycle_state": "active",
            "payload": {
                "presentation_bucket": "focus", "focus_streak_sessions": 3,
                "independent_score": 60,
            },
            "sources": [],
        }],
    }
    record = {
        "symbol": "000001.SZ", "score": 55, "rank": 500,
        "eligible": False, "selection_qualified": True,
        "classification": "neutral", "risk_name": False,
        "metrics": {"opportunity_readiness_score": 0},
    }

    snapshot = build_unified_stock_pool_snapshot(
        "current-run", date(2026, 9, 9), {"items": []}, [record],
        prior_snapshot=prior,
    )

    assert len(snapshot["items"]) == 1
    payload = snapshot["items"][0]["payload"]
    assert payload["previous_presentation_bucket"] == "focus"
    assert payload["focus_streak_sessions"] == 4


def test_bulk_stock_bars_use_causal_adjustment_and_as_of_universe() -> None:
    store = SQLiteMarketDataStore(":memory:")
    days = [date(2026, 1, day) for day in (2, 5, 6)]
    store.upsert_instruments([
        Instrument("000001.SZ", "Historical", InstrumentKind.STOCK, "SZ", False),
    ])
    store.upsert_daily_bars("test", [
        DailyBar("000001.SZ", days[0], 10, 10, 10, 10, 100),
        DailyBar("000001.SZ", days[1], 5, 5, 5, 5, 100),
        DailyBar("000001.SZ", days[2], 6, 6, 6, 6, 100),
    ])
    store.upsert_adjustment_factors("test", [
        AdjustmentFactor("000001.SZ", days[0], 1),
        AdjustmentFactor("000001.SZ", days[1], 2),
        AdjustmentFactor("000001.SZ", days[2], 2),
    ])

    bars, basis = store.get_recent_causally_adjusted_stock_bars_many(
        ["000001.SZ"], days[-1], 3,
    )

    assert [bar.close for bar in bars["000001.SZ"]] == [5, 5, 6]
    assert basis["000001.SZ"] == "forward-adjusted-as-of"
    assert store.list_active_stock_symbols_for_screening(days[-1])[0]["symbol"] == "000001.SZ"
    assert store.list_active_stock_symbols_for_screening() == []
    store.close()


def test_watch_range_phase_distinguishes_critical_breakout_and_retest() -> None:
    effective = date(2026, 6, 1)
    market = _phase_bars("000001.SH", [10.0] * 140, effective)
    critical = analyze_relative_strength(
        "CRITICAL", _phase_bars("CRITICAL", [10.0] * 140, effective, contract=True),
        effective, market_references={"market": market},
    )
    breakout = analyze_relative_strength(
        "BREAKOUT", _phase_bars("BREAKOUT", [10.0] * 139 + [10.1], effective),
        effective, market_references={"market": market},
    )
    retest = analyze_relative_strength(
        "RETEST", _phase_bars(
            "RETEST", [10.0] * 136 + [10.1, 10.08, 10.04, 10.01], effective,
        ), effective, market_references={"market": market},
    )

    assert critical["metrics"]["opportunity_phase"] == "critical"
    assert breakout["metrics"]["opportunity_phase"] == "breakout"
    assert retest["metrics"]["opportunity_phase"] == "retest"


def _phase_bars(
    symbol: str, closes: list[float], end: date, *, contract: bool = False,
) -> list[StoredDailyBar]:
    start = end - timedelta(days=len(closes) - 1)
    return [StoredDailyBar(
        symbol=symbol, trade_date=start + timedelta(days=index),
        open=close,
        high=close * (1.005 if contract and index >= len(closes) - 10 else 1.02),
        low=close * (0.995 if contract and index >= len(closes) - 10 else 0.98),
        close=close, volume=500_000 if contract and index >= len(closes) - 5 else 1_000_000,
        source="test", updated_at_ms=0,
    ) for index, close in enumerate(closes)]


def _record(
    symbol: str, classification: str, *, residual20: float,
    persistence: float, trend: str,
) -> dict[str, object]:
    strong = classification == "independent-advance"
    return {
        "symbol": symbol, "coverage_state": "complete",
        "classification": classification, "eligible": strong,
        "metrics": {
            "adjusted_residual": {"20": residual20},
            "market_excess": {"20": residual20},
            "board_excess": {"20": residual20},
            "residual_positive_ratio10": persistence,
            "downside_day_resistance": residual20,
            "trend_state": trend,
            "price_volume_confirmation": .1 if strong else 0,
            "turnover_proxy20_log": 20 if strong else 10,
            "market_correlation20": 0 if strong else -1,
        },
    }


def _bars(
    symbol: str, closes: list[float], *, start: date = date(2026, 1, 1),
) -> list[StoredDailyBar]:
    return [StoredDailyBar(
        symbol=symbol, trade_date=start + timedelta(days=index),
        open=close, high=close * 1.01, low=close * .99, close=close,
        volume=1_000_000 + index * 1000, source="test", updated_at_ms=0,
    ) for index, close in enumerate(closes)]
