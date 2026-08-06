"""Benchmark the bounded read-only MCP surface against a running StockHarness APP."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from time import perf_counter
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import anyio
from mcp import Client

from stock_harness.mcp_server import build_server


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile_value) - 1)
    return ordered[index]


def summarize(values: list[float]) -> dict[str, float | int]:
    return {
        "count": len(values),
        "total_ms": round(sum(values), 3),
        "mean_ms": round(sum(values) / len(values), 3) if values else 0.0,
        "p50_ms": round(percentile(values, 0.50), 3),
        "p95_ms": round(percentile(values, 0.95), 3),
        "max_ms": round(max(values), 3) if values else 0.0,
    }


def read_json(base_url: str, path: str, params: dict[str, str] | None = None) -> dict:
    query = urlencode(params or {})
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{query}"
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    with urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected object response from {path}")
    return payload


async def sample_chart_api(
    base_url: str,
    symbol: str,
    count: int,
    samples: list[float],
    interval_seconds: float = 0.0,
) -> None:
    for _ in range(count):
        started = perf_counter()
        await anyio.to_thread.run_sync(
            read_json,
            base_url,
            f"/api/instruments/{symbol}/daily-bars",
            {"start_date": "2026-01-01", "end_date": "2026-12-31"},
        )
        samples.append((perf_counter() - started) * 1000)
        if interval_seconds:
            await anyio.sleep(interval_seconds)


async def call_checked(client: Client, name: str, arguments: dict) -> dict:
    result = await client.call_tool(name, arguments)
    payload = result.structured_content
    if result.is_error or not isinstance(payload, dict) or not payload.get("ok"):
        raise RuntimeError(f"{name} failed: {payload}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"{name} returned invalid data")
    return data


async def benchmark_quotes(client: Client, symbols: list[str]) -> dict:
    durations: list[float] = []
    failures: list[dict[str, str]] = []
    for symbol in symbols:
        started = perf_counter()
        result = await client.call_tool("get_latest_quote", {"symbol": symbol})
        durations.append((perf_counter() - started) * 1000)
        payload = result.structured_content
        if result.is_error or not isinstance(payload, dict) or not payload.get("ok"):
            failures.append({"symbol": symbol, "result": str(payload)[:300]})
    return {
        "symbols": len(symbols),
        "latency": summarize(durations),
        "failure_count": len(failures),
        "failures": failures[:10],
    }


async def benchmark_collections(client: Client, symbols: list[str]) -> dict:
    durations: list[float] = []
    failures: list[dict[str, str]] = []
    for symbol in symbols:
        started = perf_counter()
        result = await client.call_tool(
            "get_custom_group",
            {"group_id": symbol.removeprefix("CUSTOM:"), "max_members": 500},
        )
        durations.append((perf_counter() - started) * 1000)
        payload = result.structured_content
        if result.is_error or not isinstance(payload, dict) or not payload.get("ok"):
            failures.append({"symbol": symbol, "result": str(payload)[:300]})
    return {
        "collections": len(symbols),
        "latency": summarize(durations),
        "failure_count": len(failures),
        "failures": failures[:10],
    }


async def run(base_url: str) -> dict:
    os.environ["STOCK_HARNESS_API_URL"] = base_url
    server = build_server()
    async with Client(server, raise_exceptions=False) as client:
        health = await call_checked(client, "stock_harness_health", {})
        workspace = await call_checked(client, "get_active_workspace", {})
        search = await call_checked(
            client,
            "search_instruments",
            {"query": "", "classification": "stock", "limit": 100},
        )
        catalog_symbols = [
            str(item["symbol"])
            for item in search.get("items", [])
            if isinstance(item, dict) and item.get("symbol")
        ]
        if len(catalog_symbols) < 100:
            raise RuntimeError(f"Expected 100 stock symbols, got {len(catalog_symbols)}")

        referenced_symbols = [str(value) for value in workspace.get("referenced_symbols", [])]
        collection_symbols = sorted(
            {symbol for symbol in referenced_symbols if symbol.startswith("CUSTOM:")}
        )
        direct_market_symbols = {
            symbol for symbol in referenced_symbols if not symbol.startswith("CUSTOM:")
        }
        chart_symbols = [
            str(window.get("instrument", {}).get("symbol"))
            for window in workspace.get("windows", [])
            if isinstance(window, dict)
            and window.get("type") == "chart"
            and isinstance(window.get("instrument"), dict)
            and window["instrument"].get("symbol")
        ]
        if not chart_symbols:
            raise RuntimeError("Active workspace has no chart symbol for API sampling")
        direct_market_symbols.update(chart_symbols)
        for window in workspace.get("windows", []):
            if not isinstance(window, dict):
                continue
            for value in window.get("resolved_symbols", []):
                symbol = str(value)
                if symbol and not symbol.startswith("CUSTOM:"):
                    direct_market_symbols.add(symbol)
        active_market_symbols = sorted(direct_market_symbols)

        baseline_samples: list[float] = []
        await sample_chart_api(base_url, chart_symbols[0], 20, baseline_samples)

        cases = {
            "one_symbol": await benchmark_quotes(client, catalog_symbols[:1]),
            "ten_symbols": await benchmark_quotes(client, catalog_symbols[:10]),
        }
        pressure_samples: list[float] = []
        pressure_result: dict = {}

        async def run_pressure() -> None:
            pressure_result.update(await benchmark_quotes(client, catalog_symbols[:100]))

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(run_pressure)
            tasks.start_soon(
                sample_chart_api,
                base_url,
                chart_symbols[0],
                40,
                pressure_samples,
                0.025,
            )
        cases["hundred_symbols"] = pressure_result
        cases["active_group_market_symbols"] = await benchmark_quotes(
            client, active_market_symbols
        )
        collection_case = await benchmark_collections(client, collection_symbols)

    baseline = summarize(baseline_samples)
    pressure = summarize(pressure_samples)
    baseline_p95 = float(baseline["p95_ms"])
    pressure_p95 = float(pressure["p95_ms"])
    return {
        "schema_version": "1.0",
        "base_url": base_url,
        "health": health,
        "active_workspace": {
            "group_id": workspace.get("active_group_id"),
            "group_name": workspace.get("active_group_name"),
            "window_count": len(workspace.get("windows", [])),
            "referenced_symbol_count": len(referenced_symbols),
            "direct_market_symbol_count": len(active_market_symbols),
            "collection_reference_count": len(collection_symbols),
            "chart_symbols": chart_symbols,
        },
        "quote_cases": cases,
        "active_group_collections": collection_case,
        "chart_api": {
            "symbol": chart_symbols[0],
            "baseline": baseline,
            "during_hundred_symbol_mcp": pressure,
            "p95_ratio": round(pressure_p95 / baseline_p95, 3) if baseline_p95 else None,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".tmp/test/mcp-benchmark-report.json"),
    )
    args = parser.parse_args()
    report = anyio.run(run, args.base_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
