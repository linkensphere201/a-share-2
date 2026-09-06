"""Audit a board-recognition report against persisted groups and managed tags."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import urlopen

from stock_harness.board_leader_scan import HISTORICAL_PROFILE, RECENT_PROFILE


MANAGED_TAGS = {
    (RECENT_PROFILE, 1): "近期板块龙1",
    (RECENT_PROFILE, 2): "近期板块龙2",
    (HISTORICAL_PROFILE, 1): "历史板块龙1",
    (HISTORICAL_PROFILE, 2): "历史板块龙2",
}
ALL_MANAGED_TAGS = {"板块龙1", "板块龙2", "历史高权", *MANAGED_TAGS.values()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--report", type=Path, default=Path("data/reports/board-leaders-dual-latest.json"))
    parser.add_argument("--recent-group-name", default="近期辨识度品种")
    parser.add_argument("--historical-group-name", default="历史辨识度品种")
    parser.add_argument("--expect-historical", action="append", default=[])
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    assignments = list(report["assignments"])
    errors: list[str] = []
    keys = [(item["profile"], item["board_symbol"], int(item["rank"])) for item in assignments]
    if len(keys) != len(set(keys)):
        errors.append("duplicate profile/board/rank assignments")

    groups = _get(args.base_url, "/api/custom-groups")["items"]
    details = {}
    for name in (args.recent_group_name, args.historical_group_name):
        summary = next((item for item in groups if item["name"] == name), None)
        if summary is None:
            errors.append(f"missing group: {name}")
            continue
        details[name] = _get(args.base_url, f"/api/custom-groups/{quote(str(summary['id']), safe='')}")

    expected_groups = {
        args.recent_group_name: {
            str(item["member_symbol"]) for item in assignments
            if item["profile"] == RECENT_PROFILE and int(item["rank"]) == 1
        },
        args.historical_group_name: {
            str(item["member_symbol"]) for item in assignments
            if item["profile"] == HISTORICAL_PROFILE
        },
    }
    for name, expected in expected_groups.items():
        if name not in details:
            continue
        members = details[name]["members"]
        actual = {str(item["symbol"]) for item in members}
        if actual != expected or len(actual) != len(members):
            errors.append(f"group membership mismatch: {name}")
        if any(item.get("role") != "core_identity" for item in members):
            errors.append(f"unexpected generated role: {name}")
    for symbol in args.expect_historical:
        if symbol.strip().upper() not in expected_groups[args.historical_group_name]:
            errors.append(f"expected historical symbol missing: {symbol}")

    expected_ranks: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for item in assignments:
        expected_ranks[str(item["member_symbol"])].add((str(item["profile"]), int(item["rank"])))
    symbols = _all_stock_symbols(args.base_url)
    stale_tags = 0
    mismatched_tags = 0
    managed_tag_count = 0
    for offset in range(0, len(symbols), 500):
        query = urlencode([("symbol", item) for item in symbols[offset : offset + 500]])
        for item in _get(args.base_url, f"/api/instrument-tags?{query}")["items"]:
            symbol = str(item["symbol"])
            tags = list(item["tags"])
            stale_tags += sum(tag in {"板块龙1", "板块龙2"} for tag in tags)
            actual_managed = [tag for tag in tags if tag in ALL_MANAGED_TAGS]
            managed_tag_count += len(actual_managed)
            expected_managed = [tag for key, tag in MANAGED_TAGS.items() if key in expected_ranks[symbol]]
            if any(profile == HISTORICAL_PROFILE and rank >= 3 for profile, rank in expected_ranks[symbol]):
                expected_managed.append("历史高权")
            user_tag_count = sum(tag not in ALL_MANAGED_TAGS for tag in tags)
            if actual_managed != expected_managed[:max(0, 8 - user_tag_count)]:
                mismatched_tags += 1
    if stale_tags:
        errors.append(f"stale V1 managed tags: {stale_tags}")
    if mismatched_tags:
        errors.append(f"managed tag mismatches: {mismatched_tags}")

    result = {
        "ok": not errors,
        "algorithm_version": report["algorithm_version"],
        "as_of_date": report["as_of_date"],
        "assignment_count": len(assignments),
        "recent_member_count": len(expected_groups[args.recent_group_name]),
        "historical_member_count": len(expected_groups[args.historical_group_name]),
        "managed_tag_count": managed_tag_count,
        "stock_count": len(symbols),
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


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


def _get(base_url: str, path: str) -> dict[str, object]:
    with urlopen(base_url.rstrip("/") + path, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    main()
