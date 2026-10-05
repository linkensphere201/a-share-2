"""Offline bounded fixture comparison; no provider access or live database writes."""

from datetime import date
import json
from pathlib import Path

from stock_harness.models import Instrument, InstrumentKind, DailyBar, BoardMembership
from stock_harness.sqlite_store import SQLiteMarketDataStore
from stock_harness.board_hotspot_features import extract_hotspot_session_window, extract_tracking_sessions
from stock_harness.board_hotspot_unified import UnifiedHotspotScorer
from stock_harness.hotspot_session_engine import replay_hotspot_sessions
from stock_harness.hotspot_member_history import load_hotspot_member_history


def evaluate_fixture(path: Path) -> dict:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    store = SQLiteMarketDataStore(":memory:")
    try:
        for series in fixture["series"]:
            symbol = series["symbol"]
            kind = (InstrumentKind.INDEX if symbol == "000001.SH" else
                    InstrumentKind.SECTOR if symbol in fixture["members"] else InstrumentKind.STOCK)
            store.upsert_instruments([Instrument(symbol, symbol, kind, symbol.split(".")[-1])])
            store.upsert_daily_bars("regression", [DailyBar(symbol, date.fromisoformat(d), o, h, l, c, v)
                for d, o, h, l, c, v in series["bars"]])
        for board, members in fixture["members"].items():
            store.replace_board_memberships("regression", board, date(2026, 8, 2),
                [BoardMembership(board, s, s, "regression", date(2026, 8, 2)) for s in members])
        cutoff, anchor = date(2026, 9, 30), date(2026, 9, 22)
        benchmark = store.get_recent_daily_bars("000001.SH", cutoff, 120)
        dates = [b.trade_date for b in benchmark if b.trade_date >= date(2026, 9, 1)]
        inputs = load_hotspot_member_history(store, list(fixture["members"]), dates, benchmark, lambda: None)
        old_history = {d: store.calculate_board_hotspot_snapshots(d) for d in dates}
        output = {}
        for board in fixture["members"]:
            bars = store.get_recent_daily_bars(board, cutoff, 120)
            features = extract_tracking_sessions(bars, benchmark, dates, {d: inputs.history[d][board] for d in dates})
            replay = replay_hotspot_sessions(features)
            old_features = extract_hotspot_session_window(bars, benchmark, dates, {d: old_history[d][board] for d in dates})
            rows = []
            for i, day in enumerate(dates):
                old = UnifiedHotspotScorer().score({"symbol": board, "session_features": old_features[max(0,i-4):i+1]})
                m = features[i]["metrics"]
                rows.append(dict(date=day.isoformat(), v8=old["eligible"], v9=replay[i]["active"],
                    v9_stage=replay[i]["stage"], v9_expansion=replay[i].get("expansion", False),
                    momentum=features[i]["coverage_state"] == "complete" and float((m.get("returns") or {}).get("5") or 0) >= .02,
                    relative=features[i]["coverage_state"] == "complete" and float((m.get("relative_strength") or {}).get("5") or 0) >= .02))
            first = {key: next((r["date"] for r in rows if r[key]), None) for key in ["v8", "v9", "momentum", "relative"]}
            delays = {k: sum(anchor < d <= date.fromisoformat(v) for d in dates) if v else None for k,v in first.items()}
            output[board] = dict(first_detected=first, sessions_after_research_anchor=delays,
                active_sessions={k: sum(r[k] for r in rows) for k in first},
                entry_transitions={k: sum(r[k] and (i == 0 or not rows[i-1][k]) for i,r in enumerate(rows)) for k in first},
                timeline=rows)
        return dict(source=fixture["source"], research_anchor=anchor.isoformat(), boards=output,
                    limitations=["development regression, not untouched market holdout", "current memberships", "no profitability claim"],
                    unseen_market_recall=None, false_alert_rate=None)
    finally:
        store.close()


if __name__ == "__main__":
    print(json.dumps(evaluate_fixture(Path("tests/fixtures/hotspot-auto-20260930.json")), ensure_ascii=False, indent=2))
