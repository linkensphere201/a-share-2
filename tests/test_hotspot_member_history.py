from datetime import date, timedelta

from stock_harness.hotspot_member_history import member_session_facts
from stock_harness.models import StoredDailyBar
from stock_harness.board_hotspot_unified import collapse_overlapping_hotspots


def test_aligned_stock_facts_reject_missing_session_and_future_suffix():
    dates = [date(2026, 8, 1)+timedelta(days=i) for i in range(30)]
    bars = [StoredDailyBar("S", d, 10, 11, 9, 10+i*.03, 100, "test", 0) for i,d in enumerate(dates)]
    benchmark = {d: 10 for d in dates}
    expected = member_session_facts(bars[:21], dates[:21], benchmark)
    assert member_session_facts(bars, dates[:21], benchmark) == expected
    missing = member_session_facts([b for b in bars if b.trade_date != dates[18]], dates[:21], benchmark)
    assert dates[20] not in missing


def test_overlapping_themes_group_only_same_cores_and_state():
    rows = [dict(symbol=s, radar_visible=True, total_score=90-i, leader_symbols=["S"], change_bucket="new")
            for i,s in enumerate(["A", "B", "C"])]
    members = {s: [{"symbol": f"S{i}"} for i in range(10)] for s in ["A", "B", "C"]}
    rows[2]["change_bucket"] = "risk"
    collapse_overlapping_hotspots(rows, members)
    assert rows[0]["radar_visible"] and not rows[1]["radar_visible"] and rows[2]["radar_visible"]
    assert rows[1]["related_representative"] == "A"
