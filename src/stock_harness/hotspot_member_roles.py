"""Dated role candidates for visible hotspots; no independent hotspot detector."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from math import isfinite
from statistics import fmean

from stock_harness.board_leader_scan import is_risk_name
from stock_harness.board_observation_pool import _recognition_by_board
from stock_harness.models import StoredDailyBar
from stock_harness.sqlite_store import SQLiteMarketDataStore

VERSION = "hotspot-member-roles-v1"
ROLE_LABELS = {"core-leader": "核心领涨候选", "trend-anchor": "趋势中军候选",
               "following": "跟随走强"}


def rank_hotspot_members(board_bars: Sequence[StoredDailyBar], members: Sequence[Mapping],
                         series: Mapping[str, Sequence[StoredDailyBar]],
                         raw_series: Mapping[str, Sequence[StoredDailyBar]], bases: Mapping, cutoff: date,
                         leaders: Sequence[str], recognition: Sequence[Mapping]) -> dict:
    board = sorted((b for b in board_bars if b.trade_date <= cutoff), key=lambda b: b.trade_date)[-21:]
    members = {str(m["symbol"]): m for m in members}
    report = {"version": VERSION, "effective_date": cutoff.isoformat(),
              "membership_semantics": "current-active-membership",
              "member_count": len(members), "covered_count": 0, "candidate_count": 0,
              "items": [], "exclusions": [], "status": "insufficient-data",
              "amount_basis": "raw-close-times-volume-proxy"}
    if len(board) < 21 or board[-1].trade_date != cutoff:
        return report
    dates = [b.trade_date for b in board]
    if len(set(dates)) != 21 or any(not isfinite(b.close) or b.close <= 0 for b in board):
        return report
    board_returns = [board[i].close / board[i - 5].close - 1 for i in range(5, 21)]
    board5, board20 = board_returns[-1], board[-1].close / board[0].close - 1
    board_launch = next((i for i, value in enumerate(board_returns) if value >= .03), None)
    rows = []
    for symbol, member in sorted(members.items()):
        by_date = {b.trade_date: b for b in series.get(symbol, []) if b.trade_date <= cutoff}
        raw = {b.trade_date: b for b in raw_series.get(symbol, []) if b.trade_date <= cutoff}
        if any(d not in by_date or d not in raw for d in dates):
            report["exclusions"].append({"symbol": symbol, "reason": "missing-aligned-session"})
            continue
        bars = [by_date[d] for d in dates]
        if any(not isfinite(b.close) or b.close <= 0 or b.volume <= 0 for b in bars):
            report["exclusions"].append({"symbol": symbol, "reason": "invalid-price-or-volume"})
            continue
        closes = [b.close for b in bars]
        returns = [closes[i] / closes[i - 5] - 1 for i in range(5, 21)]
        ret5, ret20 = returns[-1], closes[-1] / closes[0] - 1
        excess5, excess20 = ret5 - board5, ret20 - board20
        persistence = sum(a > max(0, b) for a, b in zip(returns[-5:], board_returns[-5:]))
        launch = next((i for i, value in enumerate(returns) if value >= .05), None)
        lead = board_launch - launch if board_launch is not None and launch is not None else None
        down = [i for i in range(1, 21) if board[i].close < board[i - 1].close]
        resilience = fmean((closes[i] / closes[i - 1] - 1) -
                          (board[i].close / board[i - 1].close - 1) for i in down) if down else None
        drawdown = closes[-1] / max(closes[-10:]) - 1
        records = [dict(r["payload"], run_id=r["source_reference"])
                   for r in recognition if r["payload"].get("member_symbol") == symbol
                   and str(r["payload"].get("effective_date", "")) <= cutoff.isoformat()]
        fresh = any(r.get("effective_date") and
                    0 <= (cutoff - date.fromisoformat(r["effective_date"])).days <= 35 for r in records)
        rows.append({"symbol": symbol, "name": member.get("name") or symbol,
                     "return_5": ret5, "return_20": ret20, "excess_return_5": excess5,
                     "excess_return_20": excess20, "strength_sessions": persistence,
                     "launch_lead_sessions": lead, "down_market_excess": resilience,
                     "down_market_sessions": len(down), "drawdown_10": drawdown,
                     "amount_proxy_5": fmean(raw[d].close * raw[d].volume for d in dates[-5:]),
                     "recognition": records, "recognition_fresh": fresh,
                     "price_basis": bases.get(symbol, "raw"),
                     "sources": sorted({b.source for b in bars}),
                     "persistent_leader": symbol in leaders})
    report["covered_count"] = len(rows)
    if len(rows) < 3 or len(rows) / max(1, len(members)) < .65:
        return report
    total_amount = sum(r["amount_proxy_5"] for r in rows)
    for rank, row in enumerate(sorted(rows, key=lambda r: (-r["amount_proxy_5"], r["symbol"])), 1):
        row["amount_rank"] = rank
        row["amount_share"] = row["amount_proxy_5"] / total_amount if total_amount else 0
    candidates = []
    for row in rows:
        if row["return_5"] <= 0 or row["return_20"] <= 0 or row["drawdown_10"] < -.15:
            continue
        if row["persistent_leader"] and row["excess_return_5"] >= .02 and row["strength_sessions"] >= 3:
            role = "core-leader"
        elif row["amount_rank"] <= max(1, min(3, len(rows) // 5)) and row["excess_return_20"] > 0 and row["strength_sessions"] >= 3:
            role = "trend-anchor"
        elif row["excess_return_5"] > 0 and row["strength_sessions"] >= 2:
            role = "following"
        else:
            continue
        components = {
            "strength": min(30, max(0, row["excess_return_5"]) * 100 + max(0, row["excess_return_20"]) * 50),
            "persistence": row["strength_sessions"] * 4,
            "liquidity": 15 * (len(rows) - row["amount_rank"]) / max(1, len(rows) - 1),
            "resilience": min(10, max(0, row["down_market_excess"] or 0) * 300),
            "lead_timing": min(10, max(0, row["launch_lead_sessions"] or 0) * 2),
            "recognition": 15 if row["recognition_fresh"] else 0,
        }
        row.update(role=role, role_label=ROLE_LABELS[role], components=components,
                   score=round(sum(components.values()), 2))
        candidates.append(row)
    candidates.sort(key=lambda r: (list(ROLE_LABELS).index(r["role"]), -r["score"], r["symbol"]))
    for rank, row in enumerate(candidates, 1):
        row["rank"] = rank
    report.update(status="complete", candidate_count=len(candidates), items=candidates[:10])
    return report


def attach_hotspot_member_roles(store: SQLiteMarketDataStore, scores: Sequence[dict], board_series: Mapping,
                                cutoff: date, check_stopping: Callable[[], None]) -> None:
    visible = [s for s in scores if s.get("radar_visible")]
    if not visible:
        return
    memberships = {}
    for score in visible:
        check_stopping()
        memberships[score["symbol"]] = [m for m in store.list_board_members(score["symbol"], 5000)
            if m.get("available") and m.get("kind") == "stock" and not is_risk_name(m["name"])
            and not str(m["symbol"]).startswith(("200", "900"))]
    symbols = sorted({m["symbol"] for members in memberships.values() for m in members})
    series, raw, bases = {}, {}, {}
    for start in range(0, len(symbols), 200):
        check_stopping()
        batch = symbols[start:start + 200]
        bars, basis = store.get_recent_causally_adjusted_stock_bars_many(batch, cutoff, 21)
        series.update(bars)
        bases.update(basis)
        raw.update(store.get_recent_daily_bars_many(batch, cutoff, 21))
    recognition = _recognition_by_board(store, cutoff, set(memberships))
    for score in visible:
        check_stopping()
        symbol = score["symbol"]
        score["hotspot_members"] = rank_hotspot_members(
            board_series.get(symbol, []), memberships[symbol], series, raw, bases, cutoff,
            score.get("leader_symbols", []), recognition.get(symbol, []))
