"""User-selected calibration cases, not an out-of-sample strategy backtest."""

from datetime import date
import hashlib
import json
from pathlib import Path

import pytest

from stock_harness.analysis_inputs import AnalysisBar
from stock_harness.first_pullback_pattern import detect_first_pullback
from stock_harness.models import DailyBar, Instrument, InstrumentKind
from stock_harness.screener import PULLBACK_STRATEGY_ID, ScreenerService
from stock_harness.sqlite_store import SQLiteMarketDataStore


SYMBOLS = ("600127.SH", "600371.SH")
DAYS = ("2026-08-19", "2026-08-20", "2026-08-21", "2026-08-24")


def fixture(symbol):
    path = Path(__file__).parent / "fixtures" / f"first_pullback_{symbol[:6]}_20260824.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert hashlib.sha256(json.dumps(data["bars"], separators=(",", ":")).encode()).hexdigest() == data["bars_sha256"]
    return data


def bars(data, cutoff):
    return tuple(AnalysisBar(
        period_start=date.fromisoformat(day), period_end=date.fromisoformat(day),
        open=o, high=h, low=l, close=c, volume=v, sources=("tushare",),
        contains_provisional=False, period_complete=True, observed_at_ms=0,
    ) for day, o, h, l, c, v in data["bars"] if day <= cutoff)


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_real_first_pullback_is_observable_within_requested_window(symbol):
    data = fixture(symbol)
    results = {day: detect_first_pullback(bars(data, day)) for day in DAYS}
    eligible = {day: result for day, result in results.items() if result and result["screen_eligible"]}
    assert eligible, results
    assert "2026-08-24" in eligible
    for day, result in eligible.items():
        assert result["stage"] == "pullback-observation"
        assert result["confirmation_date"] is None
        assert result["as_of_date"] == day
        assert result["observation_window_sessions"] == 20
        assert result["flag_window"]["qualified"]
        assert result["flag_window"]["sessions"] >= 2
        assert result["flag_window"]["end_date"] == day
        assert result["observation_window_start_date"] <= result["launch_date"]
        assert all(value <= day for key, value in result.items()
                   if key.endswith("_date") and value is not None)
    # A launch is not yet a pullback, even though its future is known in this fixture.
    assert not results["2026-08-19"] or not results["2026-08-19"]["screen_eligible"]
    assert set(eligible) == ({"2026-08-24"} if symbol == "600127.SH"
                             else {"2026-08-21", "2026-08-24"})


def seed(store, data, cutoff, poison_after=None):
    symbol = data["symbol"]
    store.upsert_instruments([Instrument(symbol, symbol, InstrumentKind.STOCK, "SH")])
    rows = []
    for day, o, h, l, c, v in data["bars"]:
        if cutoff is not None and day > cutoff:
            continue
        if poison_after is not None and day > poison_after:
            o, h, l, c, v = 1000., 1200., 1., 2., 10**10
        rows.append(DailyBar(symbol, date.fromisoformat(day), o, h, l, c, v))
    store.upsert_daily_bars("tushare", rows)
    store.upsert_trading_dates("tushare", [row.trade_date for row in rows])


@pytest.mark.parametrize("day", DAYS)
def test_screener_history_is_identical_with_missing_real_or_poisoned_future(day):
    snapshots = []
    for cutoff, poison in ((day, False), (None, False), (None, True)):
        with SQLiteMarketDataStore(":memory:") as store:
            for symbol in SYMBOLS:
                seed(store, fixture(symbol), cutoff, day if poison else None)
            run = ScreenerService(store).run_sync([], [], 10, date.fromisoformat(day), PULLBACK_STRATEGY_ID)
            assert run["status"] == "succeeded"
            candidates = store.list_screener_candidates(run["run_id"])
            evidence = {}
            for candidate in candidates:
                saved = store.get_generated_analysis_run(candidate["analysis_run_id"])
                item = next(item for item in saved["items"] if item["item_id"] == candidate["line_item_id"])
                assert candidate["evidence"] == item["payload"]
                evidence[candidate["symbol"]] = candidate["evidence"]
            snapshots.append(evidence)
    assert snapshots[0] == snapshots[1] == snapshots[2]
    if day == "2026-08-24":
        assert set(snapshots[0]) == set(SYMBOLS)
