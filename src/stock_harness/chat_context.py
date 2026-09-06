"""Deterministic, bounded context snapshots for result-bound AI turns."""

from __future__ import annotations

from datetime import date
import json
from typing import TYPE_CHECKING, Any

from stock_harness.analysis_inputs import (
    AnalysisHorizons, AnalysisInputMode, AnalysisInputService, AnalysisTimeframe,
)

if TYPE_CHECKING:
    from stock_harness.sqlite_store import SQLiteMarketDataStore


CHAT_CONTEXT_SCHEMA_VERSION = "1.1"
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
    analysis_input = AnalysisInputService(store).build(
        symbol, as_of_date, AnalysisTimeframe.DAILY,
        AnalysisInputMode.PREVIEW if run.get("expires_at_ms") is not None else AnalysisInputMode.FINAL,
        AnalysisHorizons(long=MAX_CONTEXT_BARS),
    )
    bars = analysis_input.bars[-MAX_CONTEXT_BARS:]
    items = list(run.get("items", []))[:MAX_CONTEXT_ITEMS]
    prior_summary = next((item for item in store.list_generated_analysis_runs(
        symbol, "trend", timeframe, 50
    ) if item["run_id"] != source_run_id and item["namespace"] == "official"
        and item["as_of_date"] <= as_of_date), None)
    prior = store.get_generated_analysis_run(str(prior_summary["run_id"])) if prior_summary else None
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
        "context_kind": "trend_analysis",
        "context_id": source_run_id,
        "symbol": str(run["symbol"]),
        "timeframe": timeframe,
        "source_run_id": source_run_id,
        "workspace_reference": f"analysis:{run['symbol']}:{timeframe}:{source_run_id}",
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
        "price_basis": analysis_input.price_basis,
        "volume_semantics": analysis_input.volume_semantics,
        "bars": [{
            "date": bar.period_end.isoformat(), "open": bar.open,
            "high": bar.high, "low": bar.low, "close": bar.close,
            "volume": bar.volume, "source": "+".join(bar.sources),
            "sources": list(bar.sources),
            "contains_provisional": bar.contains_provisional,
            "period_complete": bar.period_complete,
        } for bar in bars],
        "analysis_items": analysis_items,
        "evidence": evidence,
        "visible_evidence_codes": [item["code"] for item in evidence],
        "previous_official_result": None if prior is None else {
            "source_run_id": prior["run_id"],
            "as_of_date": _iso(prior["as_of_date"]),
            "input_digest": bytes(prior["input_digest"]).hex(),
            "algorithm_version": prior["algorithm_version"],
            "config_version": prior["config_version"],
            "completion_state": prior["completion_state"],
            "analysis_items": list(prior.get("items", []))[:MAX_CONTEXT_ITEMS],
            "truncated": len(prior.get("items", [])) > MAX_CONTEXT_ITEMS,
        },
        "truncated": len(run.get("items", [])) > MAX_CONTEXT_ITEMS,
    }


def build_signal_chat_context(
    store: "SQLiteMarketDataStore", *, run_id: str,
    selected_item_ids: list[str] | None = None,
) -> dict[str, object]:
    run = store.get_signal_review_run(run_id)
    if run is None or run["status"] != "succeeded":
        raise ValueError("signal chat source run does not exist or did not succeed")
    all_items = store.list_signal_review_items(run_id)
    requested = set(selected_item_ids or [])
    if len(requested) > 20:
        raise ValueError("signal chat accepts at most 20 selected items")
    if requested - {str(item["item_id"]) for item in all_items}:
        raise ValueError("selected signal items do not belong to the source run")
    items = [item for item in all_items if str(item["item_id"]) in requested]
    evidence: list[dict[str, object]] = []
    selected: list[dict[str, object]] = []
    for item in items:
        item_evidence = []
        for raw in item["evidence"]:
            value = {
                "code": str(raw["alias"]), "signal_item_id": str(item["item_id"]),
                "signal_evidence_id": str(raw["evidence_id"]),
                "kind": str(raw["evidence_type"]),
                "source_run_id": raw.get("source_run_id"),
                "source_item_id": raw.get("source_item_id"),
                "payload": raw.get("payload", {}),
            }
            evidence.append({**value, "analysis_item_id": str(raw["evidence_id"])})
            item_evidence.append(value)
        selected.append({
            "item_id": item["item_id"], "symbol": item["symbol"],
            "name": item["name"], "profile": item["profile"],
            "rank": item["rank"], "change_type": item["change_type"],
            "active": item["active"], "score": item["score"],
            "confidence": item["confidence"], "metrics": item["payload"],
            "evidence": item_evidence,
        })
    effective_date = run["effective_date"]
    assert isinstance(effective_date, date)
    return {
        "schema_version": "signal-chat-context-v1",
        "context_kind": "signal_run", "context_id": run_id,
        "source_run_id": None,
        "workspace_reference": f"signal:{run['signal_id']}:{run_id}",
        "as_of_date": effective_date.isoformat(), "input_start_date": None,
        "input_end_date": effective_date.isoformat(),
        "input_digest": str(run.get("input_digest") or ""),
        "algorithm_version": run["algorithm_version"],
        "config_version": run["definition_version"],
        "completion_state": "complete", "preview": False,
        "stale": False, "stale_reasons": [], "warnings": [],
        "signal": {
            "signal_id": run["signal_id"], "cadence": run["cadence"],
            "effective_date": effective_date.isoformat(), "revision": run["revision"],
            "prior_run_id": run["prior_run_id"], "parameters": run["parameters"],
            "summary": run["summary"],
            "diff": {"added": run["added_count"], "retained": run["retained_count"],
                     "removed": run["removed_count"]},
        },
        "selected_items": selected, "evidence": evidence,
        "visible_evidence_codes": [item["code"] for item in evidence],
        "truncated": False,
    }


def render_signal_chat_prompt(
    context: dict[str, object], user_message: str, template_instruction: str | None,
) -> str:
    instruction = template_instruction or "直接回答用户关于当前冻结信号结果的问题。"
    return "\n".join([
        "你正在分析 StockHarness 的一个不可变信号复盘结果。",
        "固定算法输出是观察事实；你的回答属于解释或质疑，不得改写信号结果。",
        "只把 selected_items 当作初始上下文；需要其他标的或证据时，按需调用 stock_harness_embedded MCP 只读工具。",
        "引用信号证据时使用快照中的 [S*] 代号，并区分算法证据、AI 推断和不确定性。",
        f"本轮模板要求：{instruction}",
        "<stockharness_signal_context>",
        json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        "</stockharness_signal_context>",
        "<user_question>", user_message.strip(), "</user_question>",
    ])


def render_chat_prompt(
    context: dict[str, object], user_message: str, template_instruction: str | None
) -> str:
    instruction = template_instruction or "直接回答用户关于当前形态分析结果的问题。"
    return "\n".join([
        "你正在分析 StockHarness 冻结的日线形态分析结果。",
        "当前标的以首个冻结快照为准；需要其他标的数据时，只能按需调用 stock_harness_embedded MCP 白名单工具。",
        "不得调用命令、文件、浏览器、网络搜索、其他 MCP 或交易工具；不得刷新 Provider 或修改市场数据。",
        "允许调用 recalculate_trend_analysis 基于已落盘日线重算指定标的，并应随后读取该标的最新分析结果。",
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
