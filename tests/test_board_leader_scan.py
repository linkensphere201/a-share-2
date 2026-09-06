from datetime import date, timedelta

from stock_harness.board_leader_scan import (
    HISTORICAL_PROFILE, RECENT_PROFILE, calculate_stock_features, compact_returns,
    is_risk_name, rank_board_leaders,
)


def _bars(scale: float, surge_every: int = 0, volume: int = 1000):
    rows = []
    close = 10.0
    start = date(2025, 1, 1)
    for index in range(180):
        change = 0.001 * scale
        if surge_every and index > 0 and index % surge_every == 0:
            change += 0.1
        close *= 1 + change
        rows.append({
            "trade_date": (start + timedelta(days=index)).isoformat(),
            "close": close, "volume": volume,
        })
    return rows


def _phased_bars(early_change: float, recent_change: float, early_surge_every: int, volume: int):
    rows = []
    close = 10.0
    start = date(2025, 1, 1)
    for index in range(180):
        change = early_change if index < 120 else recent_change
        if early_surge_every and 0 < index < 120 and index % early_surge_every == 0:
            change += 0.1
        close *= 1 + change
        rows.append({
            "trade_date": (start + timedelta(days=index)).isoformat(),
            "close": close, "volume": volume,
        })
    return rows


def test_board_leader_ranking_rewards_recognition_and_capacity():
    board = compact_returns(_bars(1.0))
    candidates = [
        calculate_stock_features("LEADER.SZ", _bars(1.5, surge_every=35, volume=50_000)),
        calculate_stock_features("SECOND.SZ", _bars(1.2, surge_every=60, volume=35_000)),
        calculate_stock_features("FOLLOWER.SZ", _bars(0.8, volume=5_000)),
        calculate_stock_features("WEAK.SZ", _bars(-0.2, volume=1_000)),
    ]
    ranked = rank_board_leaders([item for item in candidates if item], board)

    assert [item.symbol for item in ranked] == ["LEADER.SZ", "SECOND.SZ"]
    assert [item.rank for item in ranked] == [1, 2]
    assert all(0 <= item.confidence <= 1 for item in ranked)


def test_board_leader_ranking_fails_closed_for_small_board():
    board = compact_returns(_bars(1.0))
    candidate = calculate_stock_features("ONLY.SZ", _bars(1.0))
    assert rank_board_leaders([candidate] if candidate else [], board) == []


def test_recent_and_historical_profiles_reward_different_evidence():
    board = compact_returns(_bars(1.0))
    historical_bars = _phased_bars(0.001, -0.002, 24, 20_000)
    recent_bars = _phased_bars(0.0, 0.012, 0, 90_000)
    candidates = [
        calculate_stock_features("HISTORICAL.SZ", historical_bars),
        calculate_stock_features("RECENT.SZ", recent_bars),
        calculate_stock_features("CAPACITY.SZ", _bars(0.7, volume=50_000)),
        calculate_stock_features("FOLLOWER.SZ", _bars(0.4, volume=8_000)),
    ]
    eligible = [item for item in candidates if item]

    recent = rank_board_leaders(eligible, board, RECENT_PROFILE)
    historical = rank_board_leaders(eligible, board, HISTORICAL_PROFILE)

    assert recent[0].symbol != historical[0].symbol
    assert "recent" in recent[0].components
    assert "regime" in historical[0].components


def test_unknown_recognition_profile_is_rejected():
    board = compact_returns(_bars(1.0))
    candidates = [calculate_stock_features(str(index), _bars(1.0)) for index in range(4)]
    try:
        rank_board_leaders([item for item in candidates if item], board, "unsupported")
    except ValueError as exc:
        assert "unsupported" in str(exc)
    else:
        raise AssertionError("unknown profiles must fail closed")


def test_historical_profile_can_return_a_broader_ranked_memory_pool():
    board = compact_returns(_bars(1.0))
    candidates = [
        calculate_stock_features(str(index), _bars(0.5 + index * 0.1, volume=1000 + index * 1000))
        for index in range(6)
    ]

    ranked = rank_board_leaders(
        [item for item in candidates if item], board, HISTORICAL_PROFILE, limit=5
    )

    assert len(ranked) == 5
    assert [item.rank for item in ranked] == [1, 2, 3, 4, 5]


def test_risk_names_are_excluded_from_board_leader_universe():
    assert is_risk_name("ST风险")
    assert is_risk_name("*ST风险")
    assert is_risk_name("退市整理")
    assert not is_risk_name("中际旭创")
