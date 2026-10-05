from datetime import date, timedelta
from dataclasses import replace

from stock_harness.hotspot_member_roles import rank_hotspot_members, attach_hotspot_member_roles
from stock_harness.models import StoredDailyBar


DAY = date(2026, 9, 30)


def bars(symbol, step, volume=100):
    return [StoredDailyBar(symbol, DAY - timedelta(days=20-i), 10+i*step,
                          10+i*step, 10+i*step, 10+i*step, volume, "test", 0)
            for i in range(21)]


def inputs():
    series = {"CORE": bars("CORE", .35), "ANCHOR": bars("ANCHOR", .12, 10000),
              "FOLLOW": bars("FOLLOW", .08), "WEAK": bars("WEAK", -.02)}
    members = [{"symbol": s, "name": s} for s in series]
    return bars("B", .03), members, series


def run(board, members, series, recognition=()):
    return rank_hotspot_members(board, members, series, series, {}, DAY, ["CORE"], recognition)


def test_roles_and_proxy_ranking_are_distinct():
    result = run(*inputs())
    assert result["covered_count"] == 4
    roles = {r["symbol"]: r for r in result["items"]}
    assert roles["CORE"]["role"] == "core-leader"
    assert roles["ANCHOR"]["role"] == "trend-anchor"
    assert roles["ANCHOR"]["amount_rank"] == 1
    assert roles["FOLLOW"]["role"] == "undetermined"
    assert "WEAK" not in roles
    assert roles["CORE"]["down_market_excess"] is None


def test_future_suffix_and_member_order_do_not_change_report():
    board, members, series = inputs()
    expected = run(board, members, series)
    for symbol in series:
        series[symbol].append(replace(series[symbol][-1], trade_date=DAY+timedelta(days=1), close=10000))
    board.append(replace(board[-1], trade_date=DAY+timedelta(days=1), close=10000))
    assert run(board, list(reversed(members)), series) == expected


def test_missing_suspended_sessions_are_not_zero_filled():
    board, members, series = inputs()
    series["CORE"].pop(10)
    result = run(board, members, series)
    assert result["covered_count"] == 3
    assert all(r["symbol"] != "CORE" for r in result["items"])
    series["ANCHOR"].pop()
    result = run(board, members, series)
    assert result["status"] == "insufficient-data"
    assert not result["items"]


def test_recognition_has_cutoff_and_expiration_not_automatic_leadership():
    def record(day):
        return {"source_reference": "weekly", "payload": {"member_symbol": "FOLLOW",
                "rank": 1, "confidence": 1, "recognition_role": "recent-rank-1", "effective_date": day}}
    for day, bonus in [("2026-09-25", 15 * (1-5/35)), ("2026-07-01", 0), ("2026-10-01", 0)]:
        result = run(*inputs(), [record(day)])
        row = next(r for r in result["items"] if r["symbol"] == "FOLLOW")
        assert row["components"]["recognition"] == bonus
        assert row["role"] == "undetermined"
        if day > DAY.isoformat():
            assert not row["recognition"]


def test_hidden_hotspots_do_not_trigger_member_reads():
    class NoReads:
        def __getattr__(self, name):
            raise AssertionError(name)
    attach_hotspot_member_roles(NoReads(), [{"radar_visible": False}], {}, DAY, lambda: None)


def test_retained_core_survives_negative_five_day_return():
    board, members, series = inputs()
    peak = series["CORE"][-6].close
    for i in range(5):
        series["CORE"][-5+i] = replace(series["CORE"][-5+i], close=peak * (1-.008*(i+1)))
    row = next(r for r in run(board, members, series)["items"] if r["symbol"] == "CORE")
    assert row["return_5"] < 0 and row["member_state"] == "pullback"


def test_single_liquidity_spike_does_not_establish_anchor():
    board, members, series = inputs()
    series["ANCHOR"] = [replace(b, volume=1) for b in series["ANCHOR"]]
    series["ANCHOR"][-1] = replace(series["ANCHOR"][-1], volume=1000000)
    row = next(r for r in run(board, members, series)["items"] if r["symbol"] == "ANCHOR")
    assert row["amount_rank"] == 1 and row["liquidity_top_sessions"] == 1
    assert row["role"] != "trend-anchor"
