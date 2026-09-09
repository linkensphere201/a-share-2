from __future__ import annotations

from datetime import date, timedelta

from stock_harness.models import (
    BoardMembership,
    DailyBar,
    Instrument,
    InstrumentKind,
    StoredDailyBar,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.stock_observation_scan import (
    build_board_member_scan_snapshot,
    scan_full_market_independent_strength,
)
from stock_harness.stock_relative_strength import (
    analyze_relative_strength,
    classify_relative_strength,
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
    market_scan = scan_full_market_independent_strength(store, effective)
    assert len(market_scan) == 4
    assert market_scan[0]["symbol"] == "000001.SZ"
    assert market_scan[0]["classification"] == "independent-advance"
    store.close()


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
