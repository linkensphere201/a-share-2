"""Shared serialization and validation helpers for HTTP route modules."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import Request

from stock_harness.analysis_results import AnalysisNamespace
from stock_harness.api_models import AiAnalysisReportInput
from stock_harness.api_runtime import FactorLoader, StatusLoader
from stock_harness.config import FuturesExchangeCutoff
from stock_harness.intraday import IntradayQuoteService
from stock_harness.models import AdjustmentFactor, FuturesExchange, InstrumentKind, StockTradeStatus
from stock_harness.sqlite_store import SQLiteMarketDataStore


CHINA_TIME = timezone(timedelta(hours=8))


def store(request: Request) -> SQLiteMarketDataStore:
    return request.app.state.store


def materialize_custom_index(
    selected_store: SQLiteMarketDataStore,
    index_id: str,
    factor_loader: FactorLoader | None,
    status_loader: StatusLoader | None,
) -> dict[str, object]:
    detail = selected_store.get_custom_index(index_id)
    if detail is None:
        raise ValueError("custom index not found")
    symbols = [str(item["symbol"]) for item in detail["members"]]
    if factor_loader is not None:
        try:
            factors: list[AdjustmentFactor] = factor_loader(
                symbols, detail["base_date"], date.today()
            )
        except Exception as exc:
            selected_store.mark_custom_index_error(
                index_id, f"adjustment factor load failed: {exc}"
            )
            raise
        selected_store.upsert_adjustment_factors("tushare", factors)
    if status_loader is not None:
        try:
            statuses: list[StockTradeStatus] = status_loader(
                symbols, detail["base_date"], date.today()
            )
        except Exception as exc:
            selected_store.mark_custom_index_error(
                index_id, f"trade status load failed: {exc}"
            )
            raise
        selected_store.upsert_stock_trade_statuses("tushare", statuses)
    return selected_store.rebuild_custom_index(index_id)


def validate_ai_analysis_payload(
    selected_store: SQLiteMarketDataStore,
    payload: AiAnalysisReportInput,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    references = [item.model_dump(mode="json") for item in payload.references]
    by_code = {str(item["code"]): item for item in references}
    if len(by_code) != len(references):
        raise ValueError("AI analysis reference codes must be unique")
    horizons = {item.horizon for item in payload.framework.structures}
    if horizons != {"small", "medium"}:
        raise ValueError("AI analysis must contain small (7-14) and medium (14-28) structures")
    used_codes = set(payload.framework.key_level_codes)
    for structure in payload.framework.structures:
        used_codes.update(structure.reference_codes)
    for scenario in payload.framework.risk_reward:
        used_codes.update(scenario.reference_codes)
    missing = sorted(used_codes - set(by_code))
    if missing:
        raise ValueError(f"AI analysis references unknown codes: {', '.join(missing)}")
    level_codes = set(payload.framework.key_level_codes)
    if any(by_code[code]["kind"] != "level" for code in level_codes):
        raise ValueError("key_level_codes may only reference level objects")
    unmentioned = sorted(
        code for code in by_code if f"[{code}]" not in payload.conclusion_markdown
    )
    if unmentioned:
        raise ValueError(
            "AI analysis conclusion must explicitly cite every reference code: "
            + ", ".join(unmentioned)
        )

    source_items: dict[str, dict[str, object]] = {}
    if payload.source_run_id:
        candidates = [
            selected_store.get_latest_generated_analysis_run(
                payload.symbol, "trend", payload.timeframe, namespace
            )
            for namespace in (AnalysisNamespace.OFFICIAL, AnalysisNamespace.PREVIEW)
        ]
        source = next(
            (item for item in candidates if item and item["run_id"] == payload.source_run_id),
            None,
        )
        if source is None:
            raise ValueError("source_run_id must be the latest official or preview trend run")
        source_items = {str(item["item_id"]): item for item in source["items"]}

    expected_types = {"level": "zone", "line": "line", "pattern": "pattern"}
    for reference in references:
        item_id = reference.get("analysis_item_id")
        geometry = reference.get("geometry")
        if item_id:
            source_item = source_items.get(str(item_id))
            if source_item is None:
                raise ValueError(
                    f"reference {reference['code']} does not exist in source_run_id"
                )
            if source_item["item_type"] != expected_types[str(reference["kind"])]:
                raise ValueError(f"reference {reference['code']} has an incompatible item type")
            reference["snapshot"] = source_item
        elif reference["kind"] == "pattern":
            raise ValueError("custom pattern references are not supported; reference an existing item")
        elif not isinstance(geometry, dict):
            raise ValueError(f"reference {reference['code']} requires geometry")
        else:
            reference["geometry"] = validate_ai_reference_geometry(
                str(reference["kind"]), geometry
            )

    framework = payload.framework.model_dump(mode="json")
    for scenario in framework["risk_reward"]:
        entry = float(scenario["entry_price"])
        stop = float(scenario["stop_price"])
        target = float(scenario["target_price"])
        if scenario["direction"] == "long":
            risk, reward = entry - stop, target - entry
        else:
            risk, reward = stop - entry, entry - target
        if risk <= 0 or reward <= 0:
            raise ValueError(f"risk/reward scenario {scenario['name']} has invalid price ordering")
        scenario["risk_reward_ratio"] = round(reward / risk, 4)
    return framework, references


def validate_ai_reference_geometry(
    kind: str, geometry: dict[str, Any]
) -> dict[str, object]:
    if kind == "level":
        price = geometry.get("price")
        lower = geometry.get("lower", price)
        upper = geometry.get("upper", price)
        if not all(isinstance(value, (int, float)) and value > 0 for value in (lower, upper)):
            raise ValueError("level geometry requires positive price or lower/upper values")
        if float(lower) > float(upper):
            raise ValueError("level geometry lower must not exceed upper")
        return {"lower": float(lower), "upper": float(upper)}
    required = ("start_date", "start_price", "end_date", "end_price", "role")
    if any(value not in geometry for value in required):
        raise ValueError("line geometry requires start/end date, price, and role")
    try:
        start = date.fromisoformat(str(geometry["start_date"]))
        end = date.fromisoformat(str(geometry["end_date"]))
    except ValueError as error:
        raise ValueError("line geometry dates must use YYYY-MM-DD") from error
    if start >= end:
        raise ValueError("line geometry start_date must precede end_date")
    prices = (geometry["start_price"], geometry["end_price"])
    if not all(isinstance(value, (int, float)) and value > 0 for value in prices):
        raise ValueError("line geometry prices must be positive")
    if geometry["role"] not in {"support", "resistance"}:
        raise ValueError("line geometry role must be support or resistance")
    horizon = geometry.get("horizon", "small")
    if horizon not in {"small", "medium"}:
        raise ValueError("line geometry horizon must be small or medium")
    return {
        "start_date": start.isoformat(), "start_price": float(prices[0]),
        "end_date": end.isoformat(), "end_price": float(prices[1]),
        "role": geometry["role"], "horizon": horizon,
    }


def json_analysis_run(value: dict[str, object] | None) -> dict[str, object] | None:
    if value is None:
        return None
    result = dict(value)
    digest = result.get("input_digest")
    if isinstance(digest, bytes):
        result["input_digest"] = digest.hex()
    return result


def json_trend_review(value: dict[str, object]) -> dict[str, object]:
    result = dict(value)
    digest = result.get("input_digest")
    if isinstance(digest, bytes):
        result["input_digest"] = digest.hex()
    return result


def expand_subscription_symbols(
    selected_store: SQLiteMarketDataStore, symbols: list[str]
) -> list[str]:
    expanded: set[str] = set()
    for raw_symbol in symbols:
        stripped = raw_symbol.strip()
        symbol = stripped if stripped.startswith("FUT") else stripped.upper()
        if not symbol:
            continue
        if not symbol.startswith("CUSTOM:"):
            expanded.add(symbol)
            continue
        custom = selected_store.get_custom_group(symbol.split(":", 1)[1].lower())
        if custom is not None:
            expanded.update(
                value if value.startswith("FUT") else value.upper()
                for item in custom["members"]
                if (value := str(item["symbol"]).strip())
            )
    return sorted(expanded)


def normalize_instrument_symbol(symbol: str) -> str:
    stripped = symbol.strip()
    return stripped if stripped.upper().startswith("FUT") else stripped.upper()


def futures_reference_symbols(
    selected_store: SQLiteMarketDataStore, symbols: list[str]
) -> list[str]:
    result: list[str] = []
    for symbol in symbols:
        try:
            kind = str(selected_store.get_instrument_summary(symbol)["kind"])
        except (KeyError, TypeError):
            continue
        if kind in {
            InstrumentKind.FUTURES_CONTRACT.value,
            InstrumentKind.FUTURES_CONTINUOUS.value,
        }:
            result.append(symbol)
    return result


def futures_final_update_due(
    selected_store: SQLiteMarketDataStore,
    symbols: list[str],
    cutoffs: tuple[FuturesExchangeCutoff, ...],
    observed_at: datetime,
) -> bool:
    cutoff_by_exchange = {item.exchange: item.final_data_after for item in cutoffs}
    exchanges: set[FuturesExchange] = set()
    for symbol in symbols:
        summary = selected_store.get_instrument_summary(symbol)
        if summary is None:
            return False
        try:
            exchanges.add(FuturesExchange(str(summary["exchange"])))
        except ValueError:
            return False
    if not exchanges or any(exchange not in cutoff_by_exchange for exchange in exchanges):
        return False
    local = (
        observed_at.replace(tzinfo=CHINA_TIME)
        if observed_at.tzinfo is None
        else observed_at.astimezone(CHINA_TIME)
    )
    current = local.time().replace(tzinfo=None)
    return all(current >= cutoff_by_exchange[exchange] for exchange in exchanges)


def effective_intraday_items(
    selected_store: SQLiteMarketDataStore,
    service: IntradayQuoteService,
    symbols: list[str],
) -> tuple[list[dict[str, object]], list[str]]:
    items = service.list(symbols)
    effective: list[dict[str, object]] = []
    canonical_symbols: list[str] = []
    for item in items:
        symbol = str(item["symbol"]).upper()
        final_date = selected_store.get_latest_daily_bar_date(symbol)
        if final_date is None or item["trade_date"] > final_date:
            effective.append(item)
        else:
            canonical_symbols.append(symbol)
    return effective, canonical_symbols


def enrich_members(
    selected_store: SQLiteMarketDataStore,
    symbol: str,
    relation: str,
    as_of_date: object,
    source: object,
    total: int,
    items: list[dict[str, object]],
) -> dict[str, object]:
    member_symbols = [str(item["symbol"]) for item in items]
    snapshot_rows = []
    for chunk_start in range(0, len(member_symbols), 500):
        snapshot_rows.extend(selected_store.list_market_snapshots(
            member_symbols[chunk_start : chunk_start + 500]
        ))
    snapshots = {item["symbol"]: item for item in snapshot_rows}
    enriched = []
    for item in items:
        snapshot = snapshots.get(str(item["symbol"]))
        enriched.append({
            **item,
            "change_percent": snapshot["change_percent"] if snapshot else None,
            "settlement_change_percent": snapshot.get("settlement_change_percent") if snapshot else None,
            "total_market_cap": snapshot["total_market_cap"] if snapshot else None,
            "close": snapshot["close"] if snapshot else None,
            "volume": snapshot["volume"] if snapshot else None,
            "amount": snapshot["amount"] if snapshot else None,
            "open_interest": snapshot.get("open_interest") if snapshot else None,
            "open_interest_change": snapshot.get("open_interest_change") if snapshot else None,
            "source_state": snapshot.get("source_state") if snapshot else None,
            "contract_month": snapshot.get("contract_month") if snapshot else None,
            "last_trading_date": snapshot.get("last_trading_date") if snapshot else None,
            "snapshot_date": snapshot["trade_date"] if snapshot else None,
        })
    return {
        "symbol": symbol, "relation": relation,
        "as_of_date": as_of_date, "source": source,
        "total": total, "items": enriched,
    }
