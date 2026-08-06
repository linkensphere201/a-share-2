"""Run real-data acceptance for the read-only StockHarness MCP contract."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path

import anyio
from mcp import Client

from benchmark_mcp import call_checked, read_json
from stock_harness.mcp_server import build_server


SAMPLES = {
    "stock": "000001.SZ",
    "etf": "510680.SH",
    "index": "000001.SH",
    "board": "BK0696.DC",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def run(base_url: str) -> dict:
    os.environ["STOCK_HARNESS_API_URL"] = base_url
    checks: list[dict[str, object]] = []
    server = build_server()
    async with Client(server, raise_exceptions=False) as client:
        health = await call_checked(client, "stock_harness_health", {})
        require(health.get("status") == "ok", "APP health is not ok")

        instrument_results: dict[str, dict] = {}
        for kind, symbol in SAMPLES.items():
            mcp_detail = await call_checked(client, "get_instrument", {"symbol": symbol})
            direct_detail = await anyio.to_thread.run_sync(
                read_json, base_url, f"/api/instruments/{symbol}"
            )
            require(mcp_detail == direct_detail, f"{kind} instrument detail mismatch")
            require(mcp_detail.get("kind") in {kind, "sector"}, f"{symbol} kind mismatch")
            instrument_results[kind] = {
                "symbol": symbol,
                "name": mcp_detail.get("name"),
                "first_trade_date": mcp_detail.get("first_trade_date"),
                "last_trade_date": mcp_detail.get("last_trade_date"),
            }
            checks.append({"name": f"instrument_{kind}", "passed": True})

        range_args = {"start_date": "2026-08-01", "end_date": "2026-08-06"}
        history_results: dict[str, dict] = {}
        for kind, symbol in SAMPLES.items():
            mcp_history = await call_checked(
                client, "get_daily_bars", {"symbol": symbol, **range_args}
            )
            direct_history = await anyio.to_thread.run_sync(
                read_json,
                base_url,
                f"/api/instruments/{symbol}/daily-bars",
                range_args,
            )
            require(mcp_history.get("items") == direct_history.get("items"), f"{kind} OHLCV mismatch")
            items = mcp_history.get("items", [])
            require(items, f"{symbol} has no acceptance-range daily bars")
            require(
                all(item.get("bar_state") in {"final", "intraday"} for item in items),
                f"{symbol} has unlabeled bar state",
            )
            history_results[kind] = {
                "symbol": symbol,
                "rows": len(items),
                "effective_range": mcp_history.get("effective_range"),
                "latest_source": items[-1].get("source"),
                "latest_state": items[-1].get("bar_state"),
                "latest_ohlcv": {
                    key: items[-1].get(key)
                    for key in ("open", "high", "low", "close", "volume")
                },
            }
            checks.append({"name": f"ohlcv_{kind}", "passed": True})

        membership_results: dict[str, dict] = {}
        for kind in ("etf", "board"):
            symbol = SAMPLES[kind]
            arguments = {"symbol": symbol, "limit": 100, "offset": 0}
            mcp_members = await call_checked(client, "list_instrument_members", arguments)
            direct_members = await anyio.to_thread.run_sync(
                read_json,
                base_url,
                f"/api/instruments/{symbol}/members",
                {"limit": "100", "offset": "0"},
            )
            require(mcp_members == direct_members, f"{kind} membership mismatch")
            require(mcp_members.get("items"), f"{symbol} has no members")
            membership_results[kind] = {
                "symbol": symbol,
                "relation": mcp_members.get("relation"),
                "as_of_date": mcp_members.get("as_of_date"),
                "source": mcp_members.get("source"),
                "total": mcp_members.get("total"),
                "returned": len(mcp_members.get("items", [])),
            }
            checks.append({"name": f"membership_{kind}", "passed": True})

        groups = await call_checked(client, "list_custom_groups", {"limit": 200})
        group_items = groups.get("items", [])
        require(group_items, "No custom group available for acceptance")
        group_id = str(group_items[0]["id"])
        mcp_group = await call_checked(client, "get_custom_group", {"group_id": group_id})
        direct_group = await anyio.to_thread.run_sync(
            read_json, base_url, f"/api/custom-groups/{group_id}"
        )
        require(mcp_group.get("members") == direct_group.get("members"), "custom-group members mismatch")
        require(mcp_group.get("name") == direct_group.get("name"), "custom-group name mismatch")
        checks.append({"name": "custom_group", "passed": True})

        mcp_workspace = await call_checked(client, "get_active_workspace", {})
        direct_workspace = await anyio.to_thread.run_sync(
            read_json, base_url, "/api/workspace-context"
        )
        for key in ("active_group_id", "focused_window_id", "maximized_window_id", "referenced_symbols"):
            require(mcp_workspace.get(key) == direct_workspace.get(key), f"workspace {key} mismatch")
        require(
            len(mcp_workspace.get("windows", [])) == len(direct_workspace.get("windows", [])),
            "workspace window count mismatch",
        )
        chart_states = []
        direct_windows = {window["id"]: window for window in direct_workspace.get("windows", [])}
        for window in mcp_workspace.get("windows", []):
            if window.get("type") != "chart":
                continue
            direct_chart = direct_windows[window["id"]]["chart"]
            mcp_chart = window["chart"]
            for key in (
                "coordinate_mode",
                "range",
                "visible_start",
                "visible_end",
                "volume_visible",
                "indicator",
            ):
                require(mcp_chart.get(key) == direct_chart.get(key), f"chart indicator state {key} mismatch")
                require(mcp_chart.get(key) is not None, f"chart indicator state {key} is missing")
            chart_states.append({
                "symbol": window.get("instrument", {}).get("symbol"),
                "coordinate_mode": mcp_chart.get("coordinate_mode"),
                "range": mcp_chart.get("range"),
                "visible_start": mcp_chart.get("visible_start"),
                "visible_end": mcp_chart.get("visible_end"),
                "volume_visible": mcp_chart.get("volume_visible"),
                "indicator": mcp_chart.get("indicator"),
            })
        require(chart_states, "No chart state available for acceptance")
        checks.append({"name": "active_workspace_and_indicators", "passed": True})

        quote_symbol = SAMPLES["stock"]
        quote = await call_checked(client, "get_latest_quote", {"symbol": quote_symbol})
        direct_instrument = await anyio.to_thread.run_sync(
            read_json, base_url, f"/api/instruments/{quote_symbol}"
        )
        final_date = date.fromisoformat(str(direct_instrument["last_trade_date"]))
        direct_quote_bars = await anyio.to_thread.run_sync(
            read_json,
            base_url,
            f"/api/instruments/{quote_symbol}/daily-bars",
            {
                "start_date": (final_date or date.today() - timedelta(days=45)).isoformat(),
                "end_date": date.today().isoformat(),
            },
        )
        direct_items = direct_quote_bars.get("items", [])
        direct_final = next(
            (item for item in reversed(direct_items) if item.get("bar_state") == "final"),
            None,
        )
        direct_provisional = next(
            (item for item in reversed(direct_items) if item.get("bar_state") == "intraday"),
            None,
        )
        require(quote.get("latest_final") == direct_final, "latest final precedence mismatch")
        require(quote.get("provisional") == direct_provisional, "provisional precedence mismatch")
        require(quote.get("effective") == (direct_provisional or direct_final), "effective quote mismatch")
        checks.append({"name": "final_provisional_precedence", "passed": True})

    return {
        "schema_version": "1.0",
        "base_url": base_url,
        "passed": True,
        "checks": checks,
        "instruments": instrument_results,
        "history": history_results,
        "memberships": membership_results,
        "custom_group": {
            "id": group_id,
            "name": mcp_group.get("name"),
            "members": len(mcp_group.get("members", [])),
        },
        "workspace": {
            "active_group_id": mcp_workspace.get("active_group_id"),
            "active_group_name": mcp_workspace.get("active_group_name"),
            "windows": len(mcp_workspace.get("windows", [])),
            "referenced_symbols": len(mcp_workspace.get("referenced_symbols", [])),
            "chart_states": chart_states,
        },
        "latest_quote": {
            "symbol": quote_symbol,
            "precedence": quote.get("precedence"),
            "final_date": (quote.get("latest_final") or {}).get("trade_date"),
            "provisional_date": (quote.get("provisional") or {}).get("trade_date"),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".tmp/test/mcp-acceptance-report.json"),
    )
    args = parser.parse_args()
    report = anyio.run(run, args.base_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
