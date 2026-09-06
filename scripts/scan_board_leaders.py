"""Scan current StockHarness boards and optionally persist dragon-one/two labels."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, timedelta
import json
from pathlib import Path
import time
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

from stock_harness.board_leader_scan import (
    ALGORITHM_VERSION,
    HISTORICAL_PROFILE,
    RECENT_PROFILE,
    calculate_stock_features,
    compact_returns,
    is_risk_name,
    rank_board_leaders,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--max-boards", type=int)
    parser.add_argument("--board-symbol", action="append", default=[])
    parser.add_argument("--historical-limit", type=int, default=5)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--apply-report", type=Path)
    parser.add_argument("--recent-group-name", default="近期辨识度品种")
    parser.add_argument("--historical-group-name", default="历史辨识度品种")
    parser.add_argument("--group-name", help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, default=Path("data/reports/board-leaders-dual-latest.json"))
    args = parser.parse_args()

    if args.apply_report is not None:
        report = json.loads(args.apply_report.read_text(encoding="utf-8"))
        assignments = list(report["assignments"])
        if not any(item.get("profile") for item in assignments):
            assignments = [{**item, "profile": RECENT_PROFILE} for item in assignments]
        _apply(
            args.base_url,
            args.group_name or args.recent_group_name,
            args.historical_group_name,
            assignments,
            report,
            _all_stock_symbols(args.base_url),
        )
        print(json.dumps({
            "applied_report": str(args.apply_report),
            "algorithm_version": report["algorithm_version"],
            "as_of_date": report["as_of_date"],
        }, ensure_ascii=False), flush=True)
        return

    started = time.perf_counter()
    boards = _all_boards(args.base_url)
    if args.board_symbol:
        selected_symbols = {item.strip().upper() for item in args.board_symbol}
        boards = [item for item in boards if str(item["symbol"]).upper() in selected_symbols]
    if args.max_boards:
        boards = boards[:args.max_boards]
    effective_dates = [
        date.fromisoformat(str(item["last_trade_date"]))
        for item in boards
        if item.get("last_trade_date") and date.fromisoformat(str(item["last_trade_date"])) <= args.as_of
    ]
    effective_as_of = max(effective_dates, default=args.as_of)
    memberships: dict[str, list[dict[str, object]]] = {}
    stocks: dict[str, dict[str, object]] = {}
    for index, board in enumerate(boards, start=1):
        members = _get(args.base_url, f"/api/instruments/{quote(str(board['symbol']), safe='')}/members?limit=5000")["items"]
        eligible = [
            item for item in members
            if item.get("available") is not False
            and item.get("kind") == "stock"
            and not is_risk_name(str(item.get("name") or ""))
        ]
        memberships[str(board["symbol"])] = eligible
        for item in eligible:
            stocks[str(item["symbol"])] = item
        if index % 100 == 0:
            print(f"members boards={index}/{len(boards)} stocks={len(stocks)}", flush=True)

    start_date = effective_as_of - timedelta(days=args.years * 366 + 45)
    features = {}
    for index, (symbol, item) in enumerate(sorted(stocks.items()), start=1):
        bars = _bars(args.base_url, symbol, start_date, effective_as_of)
        feature = calculate_stock_features(symbol, bars)
        if feature:
            features[symbol] = feature
        if index % 100 == 0:
            print(f"stock-bars symbols={index}/{len(stocks)} eligible={len(features)}", flush=True)

    assignments: list[dict[str, object]] = []
    skipped = 0
    ranked_board_count = 0
    for index, board in enumerate(boards, start=1):
        symbol = str(board["symbol"])
        board_bars = _bars(args.base_url, symbol, start_date, effective_as_of)
        member_features = [
            features[str(item["symbol"])]
            for item in memberships[symbol]
            if str(item["symbol"]) in features
        ]
        board_series = compact_returns(board_bars)
        ranked_by_profile = {
            RECENT_PROFILE: rank_board_leaders(member_features, board_series, RECENT_PROFILE, 2),
            HISTORICAL_PROFILE: rank_board_leaders(
                member_features, board_series, HISTORICAL_PROFILE, max(2, args.historical_limit)
            ),
        }
        if any(len(items) < 2 for items in ranked_by_profile.values()):
            skipped += 1
            continue
        ranked_board_count += 1
        names = {str(item["symbol"]): str(item["name"]) for item in memberships[symbol]}
        for profile, ranked in ranked_by_profile.items():
            for item in ranked:
                assignments.append({
                    "profile": profile,
                    "board_symbol": symbol,
                    "board_name": board["name"],
                    "board_classification": board["classification"],
                    "board_source": board.get("source_label"),
                    "member_symbol": item.symbol,
                    "member_name": names.get(item.symbol, item.symbol),
                    "rank": item.rank,
                    "role": f"{profile}-recognition-{item.rank}",
                    "score": item.score,
                    "confidence": item.confidence,
                    "components": item.components,
                })
        if index % 100 == 0:
            print(f"ranking boards={index}/{len(boards)} assignments={len(assignments)}", flush=True)

    report = {
        "algorithm_version": ALGORITHM_VERSION,
        "requested_as_of_date": args.as_of.isoformat(),
        "as_of_date": effective_as_of.isoformat(),
        "membership_semantics": "current membership snapshot; not point-in-time historical membership",
        "lookback_start": start_date.isoformat(),
        "board_count": len(boards),
        "stock_count": len(stocks),
        "ranked_board_count": ranked_board_count,
        "profiles": [RECENT_PROFILE, HISTORICAL_PROFILE],
        "profile_limits": {RECENT_PROFILE: 2, HISTORICAL_PROFILE: max(2, args.historical_limit)},
        "skipped_board_count": skipped,
        "assignments": assignments,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.apply:
        _apply(
            args.base_url,
            args.group_name or args.recent_group_name,
            args.historical_group_name,
            assignments,
            report,
            list(stocks),
        )
    print(json.dumps({key: report[key] for key in (
        "algorithm_version", "as_of_date", "board_count", "stock_count",
        "ranked_board_count", "skipped_board_count", "elapsed_seconds",
    )}, ensure_ascii=False), flush=True)


def _all_boards(base_url: str) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for classification in ("concept", "industry"):
        offset = 0
        while True:
            payload = _get(base_url, "/api/instruments?" + urlencode({
                "classification": classification, "limit": 500, "offset": offset,
            }))
            result.extend(payload["items"])
            if not payload["has_more"]:
                break
            offset = int(payload["next_offset"])
    return result


def _all_stock_symbols(base_url: str) -> list[str]:
    result: list[str] = []
    offset = 0
    while True:
        payload = _get(base_url, "/api/instruments?" + urlencode({
            "classification": "stock", "limit": 500, "offset": offset,
        }))
        result.extend(str(item["symbol"]) for item in payload["items"])
        if not payload["has_more"]:
            return result
        offset = int(payload["next_offset"])


def _bars(base_url: str, symbol: str, start: date, end: date) -> list[dict[str, object]]:
    return _get(base_url, f"/api/instruments/{quote(symbol, safe='')}/daily-bars?" + urlencode({
        "start_date": start.isoformat(), "end_date": end.isoformat(),
    }))["items"]


def _apply(
    base_url: str,
    recent_group_name: str,
    historical_group_name: str,
    assignments: list[dict[str, object]],
    report: dict[str, object],
    universe_symbols: list[str],
) -> None:
    by_symbol: dict[str, list[dict[str, object]]] = defaultdict(list)
    ranks: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for item in assignments:
        by_symbol[str(item["member_symbol"])].append(item)
        ranks[str(item["member_symbol"])].add((str(item["profile"]), int(item["rank"])))
    managed_tags = {
        (RECENT_PROFILE, 1): "近期板块龙1",
        (RECENT_PROFILE, 2): "近期板块龙2",
        (HISTORICAL_PROFILE, 1): "历史板块龙1",
        (HISTORICAL_PROFILE, 2): "历史板块龙2",
    }
    managed_tag_names = {"板块龙1", "板块龙2", "历史高权", *managed_tags.values()}
    existing_tags: dict[str, list[str]] = {}
    for offset in range(0, len(universe_symbols), 500):
        query = urlencode([("symbol", item) for item in universe_symbols[offset : offset + 500]])
        for item in _get(base_url, f"/api/instrument-tags?{query}")["items"]:
            existing_tags[str(item["symbol"])] = list(item["tags"])
    symbols = sorted(set(by_symbol) | {
        symbol for symbol, tags in existing_tags.items()
        if managed_tag_names.intersection(tags)
    })
    for index, symbol in enumerate(symbols, start=1):
        retained = [tag for tag in existing_tags.get(symbol, []) if tag not in managed_tag_names]
        managed = [tag for key, tag in managed_tags.items() if key in ranks[symbol]]
        if any(profile == HISTORICAL_PROFILE and rank >= 3 for profile, rank in ranks[symbol]):
            managed.append("历史高权")
        retained = retained[:8]
        available = max(0, 8 - len(retained))
        _request(base_url, f"/api/instruments/{quote(symbol, safe='')}/tags", "PUT", {"tags": retained + managed[:available]})
        if index % 100 == 0:
            print(f"tags updated={index}/{len(symbols)}", flush=True)

    groups = _get(base_url, "/api/custom-groups")["items"]
    _upsert_recognition_group(
        base_url,
        groups,
        recent_group_name,
        assignments,
        report,
        RECENT_PROFILE,
        {1},
        aliases={"全市场辨识度品种"},
    )
    if any(item.get("profile") == HISTORICAL_PROFILE for item in assignments):
        _upsert_recognition_group(
            base_url,
            groups,
            historical_group_name,
            assignments,
            report,
            HISTORICAL_PROFILE,
            {int(item["rank"]) for item in assignments if item.get("profile") == HISTORICAL_PROFILE},
        )


def _upsert_recognition_group(
    base_url: str,
    groups: list[dict[str, object]],
    group_name: str,
    assignments: list[dict[str, object]],
    report: dict[str, object],
    profile: str,
    included_ranks: set[int],
    aliases: set[str] | None = None,
) -> None:
    algorithm_version = str(report.get("algorithm_version") or ALGORITHM_VERSION)
    selected = [
        item for item in assignments
        if item.get("profile") == profile and int(item["rank"]) in included_ranks
    ]
    by_symbol: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in selected:
        by_symbol[str(item["member_symbol"])].append(item)
    members = []
    for symbol, items in sorted(
        by_symbol.items(),
        key=lambda pair: (-len(pair[1]), -max(float(item["score"]) for item in pair[1]), pair[0]),
    ):
        ordered = sorted(items, key=lambda row: (int(row["rank"]), -float(row["score"])))
        board_names = [str(item["board_name"]) for item in ordered]
        members.append({
            "symbol": symbol,
            "role": "core_identity",
            "tags": [f"{item['board_name']}{'近期' if profile == RECENT_PROFILE else '历史'}龙{item['rank']}" for item in ordered[:12]],
            "note": (
                f"{algorithm_version}；截至{report['as_of_date']}；"
                f"{'近期高权' if profile == RECENT_PROFILE else '历史高权'}板块：" + "、".join(board_names)
            )[:500],
        })
    payload = {
        "name": group_name,
        "description": (
            f"{algorithm_version} 日线量价代理排名；截至 {report['as_of_date']}；"
            f"{'强调近期强度、容量和板块联动' if profile == RECENT_PROFILE else '强调跨阶段重复活跃、历史峰值和长期板块联动'}；"
            "使用当前成分关系，不代表永久龙头。"
        ),
        "members": members,
    }
    exact = next((item for item in groups if item["name"] == group_name), None)
    if exact is None:
        accepted_aliases = aliases or set()
        exact = next((item for item in groups if item["name"] in accepted_aliases), None)
    if exact:
        _request(base_url, f"/api/custom-groups/{exact['id']}", "PUT", payload)
    else:
        created = _request(base_url, "/api/custom-groups", "POST", payload)
        groups.append(created)


def _get(base_url: str, path: str) -> dict[str, object]:
    return _request(base_url, path, "GET")


def _request(base_url: str, path: str, method: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = Request(base_url.rstrip("/") + path, data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json; charset=utf-8")
    for attempt in range(3):
        try:
            with urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            if attempt == 2:
                raise
            time.sleep(0.3 * (attempt + 1))
    raise AssertionError("unreachable")


if __name__ == "__main__":
    main()
