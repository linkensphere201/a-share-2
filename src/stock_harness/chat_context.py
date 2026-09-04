"""Deterministic, bounded context snapshots for result-bound AI turns."""

from __future__ import annotations

from datetime import date
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from stock_harness.sqlite_store import SQLiteMarketDataStore


CHAT_CONTEXT_SCHEMA_VERSION = "1.0"
MAX_CONTEXT_BARS = 250
MAX_CONTEXT_ITEMS = 160


def build_chat_context(
    store: "SQLiteMarketDataStore",
    *,
    symbol: str,
    timeframe: str,
    source_run_id: str,
) -> dict[str, object]:
    run = store.get_generated_analysis_run(source_run_id)
    if run is None:
        raise ValueError("chat source run does not exist or did not succeed")
    if str(run["symbol"]) != symbol.strip().upper() or timeframe != "daily":
        raise ValueError("chat context must match a daily analysis run for the symbol")
    as_of_date = run["as_of_date"]
    assert isinstance(as_of_date, date)
    bars = store.get_recent_daily_bars(symbol, as_of_date, MAX_CONTEXT_BARS)
    items = list(run.get("items", []))[:MAX_CONTEXT_ITEMS]
    counters = {"zone": 0, "line": 0, "pattern": 0}
    prefixes = {"zone": "K", "line": "L", "pattern": "P"}
    evidence: list[dict[str, object]] = []
    analysis_items: list[dict[str, object]] = []
    for raw in items:
        item = dict(raw)
        item_type = str(item.get("item_type", ""))
        code: str | None = None
        if item_type in counters:
            counters[item_type] += 1
            code = f"{prefixes[item_type]}{counters[item_type]}"
            evidence.append({
                "code": code,
                "analysis_item_id": str(item.get("item_id", "")),
                "kind": item_type,
            })
        analysis_items.append({
            "code": code,
            "analysis_item_id": str(item.get("item_id", "")),
            "item_type": item_type,
            "parent_item_id": item.get("parent_item_id"),
            "payload": item.get("payload", {}),
        })
    return {
        "schema_version": CHAT_CONTEXT_SCHEMA_VERSION,
        "symbol": str(run["symbol"]),
        "timeframe": timeframe,
        "source_run_id": source_run_id,
        "as_of_date": as_of_date.isoformat(),
        "input_start_date": _iso(run.get("input_start_date")),
        "input_end_date": _iso(run.get("input_end_date")),
        "input_digest": bytes(run["input_digest"]).hex(),
        "algorithm_version": str(run["algorithm_version"]),
        "config_version": str(run["config_version"]),
        "completion_state": str(run["completion_state"]),
        "preview": run.get("expires_at_ms") is not None,
        "source_observed_at_ms": run.get("source_observed_at_ms"),
        "stale": bool(run.get("stale")),
        "stale_reasons": list(run.get("stale_reasons", [])),
        "warnings": list(run.get("warnings", [])),
        "bars": [{
            "date": bar.trade_date.isoformat(), "open": bar.open,
            "high": bar.high, "low": bar.low, "close": bar.close,
            "volume": bar.volume, "source": bar.source,
        } for bar in bars],
        "analysis_items": analysis_items,
        "evidence": evidence,
        "truncated": len(run.get("items", [])) > MAX_CONTEXT_ITEMS,
    }


def render_chat_prompt(
    context: dict[str, object], user_message: str, template_instruction: str | None
) -> str:
    instruction = template_instruction or "直接回答用户关于当前形态分析结果的问题。"
    return "\n".join([
        "你正在分析 StockHarness 冻结的日线形态分析结果。",
        "只能使用下方快照中的数据，不得调用工具、执行命令、读取文件或修改任何记录。",
        "区分事实、规则推断和不确定性；不得把盘中预览表述为正式收盘数据。",
        "引用关键位、趋势线或形态时使用快照给出的 [K*]、[L*]、[P*] 代号。",
        "回答仅用于研究，不得宣称保证收益或自动执行交易。",
        f"本轮模板要求：{instruction}",
        "<stockharness_context>",
        json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        "</stockharness_context>",
        "<user_question>",
        user_message.strip(),
        "</user_question>",
    ])


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, date) else None
