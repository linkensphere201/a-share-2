from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path

from stock_harness.board_hotspot_unified import UnifiedHotspotScorer, apply_discovery_visibility
from stock_harness.board_hotspot_features import extract_hotspot_session_window
from stock_harness.models import BoardMembership, DailyBar, Instrument, InstrumentKind
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.board_observation_pool import _build_board_pool_snapshot
from stock_harness.review_scoring import score_entities


def window():
    return [{
        "effective_date": f"2026-09-{day:02}", "coverage_state": "complete",
        "metrics": {"returns": {"1": .01, "5": .024, "20": .017},
                    "relative_strength": {"5": .052}, "recent_volume_ratio_5_5": 1.13,
                    "hotspot_shape": {"path": "none", "extension_from_ma20_atr": 1.06}},
        "member_snapshot": {"coverage_ratio": 1, "member_count": 24,
                            "return_5_covered_count": 24, "positive_return_5_ratio": .478,
                            "trend_leader_symbols": ["600418.SH"]},
    } for day in [23, 24, 28, 29, 30]]


def score(features):
    return UnifiedHotspotScorer().score({"symbol": "881125.TI", "session_features": features})


def test_core_led_without_current_limit_up_or_board_breakout():
    result = score(window())
    assert result["eligible"] and result["hotspot_stage"] == "leader-ignited"
    assert result["leader_symbols"] == ["600418.SH"]
    assert result["hotspot_window_observed_sessions"] == 5
    assert "without-broad-diffusion" in result["risk_summary"]


def test_broad_trend_does_not_require_limit_up():
    features = window()
    for item in features:
        item["member_snapshot"].update(positive_return_5_ratio=.7, trend_leader_symbols=[])
        item["metrics"]["returns"]["5"] = .05
        item["metrics"]["hotspot_shape"]["path"] = "trend-continuation"
    assert score(features)["hotspot_stage"] == "hotspot-confirmed"


def test_pulse_rotating_leaders_missing_sessions_and_reversal_are_not_hotspots():
    for mode in ["pulse", "rotating", "missing", "reversal", "coverage"]:
        features = window()
        if mode == "pulse":
            for item in features[:-1]:
                item["metrics"]["returns"]["5"] = -.02
        elif mode == "rotating":
            for index, item in enumerate(features):
                item["member_snapshot"]["trend_leader_symbols"] = [str(index)]
        elif mode == "missing":
            features[-2]["coverage_state"] = "missing-session"
        elif mode == "coverage":
            features[-1]["member_snapshot"]["return_5_covered_count"] = 8
        else:
            features[-1]["metrics"]["returns"]["1"] = -.06
        assert not score(features)["eligible"], mode


def test_market_concentration_are_context_and_aliases_collapse():
    first = score(window())
    second = {**deepcopy(first), "symbol": "BK1029.DC"}
    names = {first["symbol"]: "汽车整车", second["symbol"]: "汽车整车"}
    apply_discovery_visibility([first, second], names, {},
                              {"capacity_tier": "low", "direction": "contracting"},
                              {first["symbol"]: {"turnover_concentration_hhi": .8}})
    assert sum(row["radar_visible"] for row in [first, second]) == 1
    assert "concentrated-participation" in first["risk_summary"]
    assert next(row for row in [first, second] if not row["radar_visible"])["visibility_reasons"] == ["same-theme-representative"]


def test_window_is_prefix_bounded_and_marks_missing_bar():
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([Instrument("B", "Board", InstrumentKind.SECTOR, "DC")])
    start = date(2026, 1, 1)
    store.upsert_daily_bars("test", [DailyBar("B", start + timedelta(days=i), 10, 11, 9, 10 + i / 100, 100) for i in range(70)])
    bars = store.get_recent_daily_bars("B", start + timedelta(days=69), 70)
    days = [start + timedelta(days=i) for i in range(60, 65)]
    snapshots = {day: window()[0]["member_snapshot"] for day in days}
    prefix = extract_hotspot_session_window(bars[:65], bars[:65], days, snapshots)
    assert prefix == extract_hotspot_session_window(bars, bars, days, snapshots)
    broken = extract_hotspot_session_window([b for b in bars if b.trade_date != days[2]], bars, days, snapshots)
    assert broken[2]["coverage_state"] == "missing-session"
    assert broken[2]["effective_date"] == days[2].isoformat()
    store.close()


def test_hotspot_members_exclude_stale_latest_bar():
    store = SQLiteMarketDataStore(":memory:")
    store.upsert_instruments([Instrument("B", "Board", InstrumentKind.SECTOR, "DC"),
                              Instrument("S", "Stock", InstrumentKind.STOCK, "SH")])
    day = date(2026, 9, 30)
    store.replace_board_memberships("test", "B", day, [BoardMembership("B", "S", "Stock", "test", day)])
    store.upsert_daily_bars("test", [DailyBar("S", day - timedelta(days=i), 10, 11, 9, 10, 100) for i in range(1, 8)])
    result = store.calculate_board_hotspot_snapshots(day)["B"]
    assert result["covered_member_count"] == 0
    assert result["trend_leader_symbols"] == []
    store.close()


def test_core_stock_activity_does_not_require_broad_board_volume():
    features = window()
    for item in features:
        item["metrics"]["recent_volume_ratio_5_5"] = .9
    result = score(features)
    assert result["eligible"]
    assert result["candidate_streak"] == 3
    assert result["hotspot_window_qualified_sessions"] == 5
    for item in features:
        item["member_snapshot"]["trend_leader_symbols"] = []
    assert not score(features)["eligible"]


def test_review_history_cadence_does_not_change_discovery():
    entities = [{"symbol": "881125.TI", "session_features": window()}]
    scorer = UnifiedHotspotScorer()
    initial = score_entities(scorer, entities)[0]
    for count in [1, 5, 20]:
        prior = {**initial, "total_score": 1, "eligible": False}
        result = score_entities(scorer, entities,
            prior_by_symbol={"881125.TI": prior},
            recent_by_symbol={"881125.TI": [prior] * count})[0]
        for field in ["eligible", "total_score", "hotspot_stage", "leader_symbols",
                      "hotspot_window_dates", "hotspot_window_qualified_sessions"]:
            assert result[field] == initial[field]


def test_repeated_dates_and_tiny_memberships_are_not_persistence():
    features = window()
    features[-1]["effective_date"] = features[-2]["effective_date"]
    assert "incomplete-session-window" in score(features)["disqualifiers"]
    features = window()
    for item in features:
        item["member_snapshot"].update(member_count=2, return_5_covered_count=2)
    assert not score(features)["eligible"]


def test_member_leaders_require_sustained_own_volume_not_one_spike():
    store = SQLiteMarketDataStore(":memory:")
    day = date(2026, 9, 30)
    store.upsert_instruments([Instrument("B", "Board", InstrumentKind.SECTOR, "DC")]
                            + [Instrument(s, s, InstrumentKind.STOCK, "SH") for s in ["GOOD", "SPIKE", "QUIET"]])
    store.replace_board_memberships("test", "B", day,
                                   [BoardMembership("B", s, s, "test", day) for s in ["GOOD", "SPIKE", "QUIET"]])
    bars = []
    for symbol in ["GOOD", "SPIKE", "QUIET"]:
        for i in range(10):
            close = 10 if i >= 5 else 12
            volume = 100 if i >= 5 or symbol == "QUIET" else 120
            if symbol == "SPIKE" and i == 0:
                volume = 3000
            bars.append(DailyBar(symbol, day - timedelta(days=i), close, close, close, close, volume))
    store.upsert_daily_bars("test", bars)
    assert store.calculate_board_hotspot_snapshots(day)["B"]["trend_leader_symbols"] == ["GOOD"]
    store.close()


def test_hidden_unified_hotspots_do_not_reenter_board_pool():
    store = SQLiteMarketDataStore(":memory:")
    result = score(window())
    result.update(radar_visible=False, rank=1, entity_key="board:881125.TI")
    pool = _build_board_pool_snapshot(store, "test", date(2026, 9, 30), [], [], None, [result])
    assert not pool["items"]
    store.close()


def test_real_auto_case_uses_complete_source_without_inventing_missing_bars():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "hotspot-auto-20260930.json").read_text(encoding="utf-8"))
    store = SQLiteMarketDataStore(":memory:")
    for series in fixture["series"]:
        symbol = series["symbol"]
        kind = (InstrumentKind.INDEX if symbol == "000001.SH" else
                InstrumentKind.SECTOR if symbol in fixture["members"] else InstrumentKind.STOCK)
        store.upsert_instruments([Instrument(symbol, symbol, kind, symbol.split(".")[-1])])
        store.upsert_daily_bars("mcp-regression", [DailyBar(symbol, date.fromisoformat(d), o, h, l, c, v)
                                                for d, o, h, l, c, v in series["bars"]])
    for board, members in fixture["members"].items():
        store.replace_board_memberships("mcp-regression", board, date(2026, 8, 2),
            [BoardMembership(board, s, s, "mcp-regression", date(2026, 8, 2)) for s in members])
    cutoff = date(2026, 9, 30)
    benchmark = store.get_recent_daily_bars("000001.SH", cutoff, 120)
    days = [b.trade_date for b in benchmark[-5:]]
    history = {day: store.calculate_board_hotspot_snapshots(day) for day in days}
    results = {}
    for board in fixture["members"]:
        features = extract_hotspot_session_window(store.get_recent_daily_bars(board, cutoff, 120),
                     benchmark, days, {day: history[day][board] for day in days})
        results[board] = UnifiedHotspotScorer().score({"symbol": board, "session_features": features})
    assert results["BK1029.DC"]["eligible"]
    assert results["BK1029.DC"]["hotspot_stage"] == "leader-ignited"
    assert "600418.SH" in results["BK1029.DC"]["leader_symbols"]
    assert not results["881125.TI"]["eligible"]
    assert "incomplete-session-window" in results["881125.TI"]["disqualifiers"]
    apply_discovery_visibility(list(results.values()), {b: "汽车整车" for b in results}, {}, {}, {})
    assert results["BK1029.DC"]["radar_visible"]
    assert not results["881125.TI"]["radar_visible"]
    store.close()
