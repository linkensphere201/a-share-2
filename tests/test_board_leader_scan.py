from datetime import date, timedelta

from stock_harness.board_leader_scan import (
    calculate_stock_features, compact_returns, is_risk_name, rank_board_leaders,
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


def test_risk_names_are_excluded_from_board_leader_universe():
    assert is_risk_name("ST风险")
    assert is_risk_name("*ST风险")
    assert is_risk_name("退市整理")
    assert not is_risk_name("中际旭创")
