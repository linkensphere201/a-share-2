"""Request-local stock facts reused across all board/session aggregates."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from statistics import fmean

from stock_harness.board_leader_scan import is_risk_name
from stock_harness.models import StoredDailyBar

REPLAY_SESSIONS = 60


@dataclass
class HotspotMemberInputs:
    memberships: dict
    series: dict
    raw: dict
    bases: dict
    history: dict


def member_session_facts(bars: Sequence[StoredDailyBar], calendar: Sequence[date],
                         benchmark: dict[date, float]) -> dict[date, dict]:
    by_date = {bar.trade_date: bar for bar in bars}
    facts = {}
    for index in range(10, len(calendar)):
        day = calendar[index]
        dates = calendar[index-10:index+1]
        if any(d not in by_date for d in dates):
            continue
        values = [by_date[d] for d in dates]
        if any(b.close <= 0 or b.volume <= 0 for b in values):
            continue
        closes = [b.close for b in values]
        ret1, ret5 = closes[-1] / closes[-2] - 1, closes[-1] / closes[-6] - 1
        if any(abs(closes[i] / closes[i-1] - 1) > .45 for i in range(1, len(closes))):
            continue
        if not all(d in benchmark and benchmark[d] > 0 for d in [dates[-1], dates[-2], dates[-6]]):
            continue
        rs1 = ret1 - (benchmark[dates[-1]] / benchmark[dates[-2]] - 1)
        rs5 = ret5 - (benchmark[dates[-1]] / benchmark[dates[-6]] - 1)
        activity = values[-1].volume / fmean(b.volume for b in values[:-1])
        up_days = sum(closes[i] > closes[i-1] for i in range(8, 11))
        strong = ret5 >= .04 and rs5 >= .025
        impulse = (ret1 >= .025 and rs1 >= .02 and activity >= 1.2) or (
            ret5 >= .07 and rs5 >= .04 and up_days >= 2 and activity >= 1.05)
        facts[day] = dict(return_1=ret1, return_5=ret5, strong=strong, impulse=impulse,
                          healthy=closes[-1] / max(closes) >= .9 and closes[-1] >= fmean(closes))
    return facts


def load_hotspot_member_history(store, board_symbols: Sequence[str], dates: Sequence[date],
                                benchmark_bars: Sequence[StoredDailyBar],
                                check_stopping: Callable[[], None]) -> HotspotMemberInputs:
    if not dates:
        return HotspotMemberInputs({}, {}, {}, {}, {})
    memberships = {}
    for start in range(0, len(board_symbols), 200):
        check_stopping()
        page = store.list_hotspot_memberships(board_symbols[start:start+200])
        memberships.update({board: [m for m in members if not is_risk_name(m["name"])
                            and not m["symbol"].startswith(("200", "900"))] for board, members in page.items()})
    symbols = sorted({m["symbol"] for members in memberships.values() for m in members})
    series, raw, bases = {}, {}, {}
    facts = {}
    cutoff = dates[-1]
    benchmark = {b.trade_date: b.close for b in benchmark_bars if b.trade_date <= cutoff}
    calendar = sorted(set(dates) | {d for d in benchmark if d < dates[0]})[-len(dates)-21:]
    for start in range(0, len(symbols), 200):
        check_stopping()
        page = symbols[start:start+200]
        adjusted, basis = store.get_recent_causally_adjusted_stock_bars_many(page, cutoff, len(calendar))
        series.update(adjusted)
        bases.update(basis)
        raw.update(store.get_recent_daily_bars_many(page, cutoff, len(calendar)))
        for symbol in page:
            facts[symbol] = member_session_facts(adjusted.get(symbol, []), calendar, benchmark)
    history = {day: {} for day in dates}
    for board, members in memberships.items():
        check_stopping()
        for day in dates:
            rows = [(m["symbol"], facts[m["symbol"]][day]) for m in members if day in facts[m["symbol"]]]
            n = len(rows)
            history[day][board] = dict(member_count=len(members), covered_member_count=n,
                return_5_covered_count=n, coverage_ratio=n / len(members) if members else 0,
                positive_return_1_ratio=sum(r["return_1"] > 0 for _, r in rows) / n if n else 0,
                positive_return_5_ratio=sum(r["return_5"] > 0 for _, r in rows) / n if n else 0,
                strong_member_symbols=[s for s, r in rows if r["strong"]],
                impulse_leader_symbols=[s for s, r in rows if r["impulse"]],
                healthy_member_symbols=[s for s, r in rows if r["healthy"]],
                covered_member_symbols=[s for s, _ in rows],
                membership_semantics="current-active-membership")
    return HotspotMemberInputs(memberships, series, raw, bases, history)
