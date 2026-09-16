"""Bounded MCP export and offline replay; never reads the live market database."""

import argparse
import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
import time

from stock_harness.low_base_pullback import ALGORITHM_VERSION, CONFIG
from stock_harness.low_base_validation import POLICY, replay_snapshot


PREFIXES = ("0000", "0020", "3000", "6000", "6030", "6880")
EXCLUDED = {"001258.SZ", "600127.SH", "600371.SH", "002084.SZ", "301520.SZ"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def validate_manifest(snapshot, manifest):
    if digest(manifest) != snapshot["manifest_sha256"]:
        raise ValueError("snapshot manifest checksum mismatch")
    source_hash = hashlib.sha256(Path("src/stock_harness/low_base_pullback.py").read_bytes()).hexdigest()
    if (manifest["detector_source_sha256"] != source_hash or manifest["parameters"] != asdict(CONFIG)
        or manifest["policy"] != asdict(POLICY) or manifest["algorithm_version"] != ALGORITHM_VERSION):
        raise ValueError("frozen detector/policy changed; register a new experiment instead")


async def collect(output, api_url):
    from mcp.client import ClientSession
    from mcp.client.stdio import stdio_client, StdioServerParameters
    output.mkdir(parents=True, exist_ok=False)
    params = StdioServerParameters(command=sys.executable, args=["-m", "stock_harness.mcp_server"],
                                  env={"STOCK_HARNESS_API_URL": api_url, "STOCK_HARNESS_MCP_TIMEOUT_SECONDS": "20"})
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            async def call(tool, args):
                result = await session.call_tool(tool, args)
                envelope = json.loads(next(c.text for c in result.content if c.type == "text"))
                if not envelope.get("ok"):
                    raise RuntimeError(json.dumps(envelope, ensure_ascii=True))
                return envelope["data"]
            await call("stock_harness_health", {})
            cohort, searches = [], []
            for prefix in PREFIXES:
                data = await call("search_instruments", {"query": prefix, "classification": "stock", "limit": 100})
                eligible = [i for i in data["items"] if i["symbol"].startswith(prefix)
                            and i["kind"] == "stock" and i["symbol"] not in EXCLUDED]
                selected = sorted(eligible, key=lambda i: hashlib.sha256(
                    f"low-base-validation-v1:{i['symbol']}".encode()).hexdigest())[:10]
                searches.append({"prefix": prefix, "has_more": data.get("has_more"),
                                 "eligible_count": len(eligible), "selected_count": len(selected),
                                 "eligible_symbols": sorted(i["symbol"] for i in eligible)})
                cohort.extend({k: i.get(k) for k in ("symbol", "name", "active", "first_trade_date", "last_trade_date")} for i in selected)
            manifest = {"created_at": datetime.now(UTC).isoformat(), "algorithm_version": ALGORITHM_VERSION,
                        "detector_source_sha256": hashlib.sha256(Path("src/stock_harness/low_base_pullback.py").read_bytes()).hexdigest(),
                        "parameters": asdict(CONFIG), "policy": asdict(POLICY), "sampling": searches,
                        "exclusions": sorted(EXCLUDED), "cohort": cohort,
                        "data_range": ["2025-01-01", "2026-09-15"], "max_bars": 500}
            save(output / "manifest.json", manifest)
            print(f"Manifest frozen before OHLCV: {len(cohort)} symbols; sha256={digest(manifest)}", flush=True)
            instruments = []
            for index, item in enumerate(cohort, 1):
                try:
                    data = await call("get_daily_bars", {"symbol": item["symbol"], "start_date": "2025-01-01",
                                                         "end_date": "2026-09-15", "max_bars": 500})
                    row = {**item, "bars": data["items"], "truncated": data["truncated"],
                           "freshness": data["freshness"], "bars_sha256": digest(data["items"])}
                except Exception as exc:
                    row = {**item, "bars": [], "error": str(exc)}
                instruments.append(row)
                print(f"{index}/{len(cohort)} {item['symbol']} bars={len(row['bars'])} error={row.get('error', '')}", flush=True)
            snapshot = {"manifest_sha256": digest(manifest), "price_basis": "raw-unadjusted",
                        "source": "read-only StockHarness MCP / get_daily_bars", "instruments": instruments}
            save(output / "snapshot.json", snapshot)
            return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--api-url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    started = time.perf_counter()
    if args.snapshot:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        manifest = json.loads((args.snapshot.parent / "manifest.json").read_text(encoding="utf-8"))
        validate_manifest(snapshot, manifest)
        args.output.mkdir(parents=True, exist_ok=False)
    else:
        snapshot = asyncio.run(collect(args.output, args.api_url))
    for item in snapshot["instruments"]:
        if "bars_sha256" in item and digest(item["bars"]) != item["bars_sha256"]:
            raise ValueError(f"snapshot checksum mismatch: {item['symbol']}")
    report = replay_snapshot(snapshot)
    report["snapshot_sha256"] = digest(snapshot)
    report["evaluation_source_sha256"] = hashlib.sha256(Path("src/stock_harness/low_base_validation.py").read_bytes()).hexdigest()
    report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    save(args.output / "report.json", report)
    print(json.dumps({k: report[k] for k in ("by_stage", "by_period", "unique_seeds", "screened_symbol_days", "elapsed_seconds")},
                     ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
