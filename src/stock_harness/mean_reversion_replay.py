"""Fast, non-persisting mean-reversion adapter for the shared replay engine."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Callable, Protocol

from stock_harness.board_leader_scan import is_risk_name
from stock_harness.mean_reversion_facts import build_mean_reversion_facts
from stock_harness.mean_reversion_context import build_market_permission
from stock_harness.mean_reversion_policy import MeanReversionPolicyConfig
from stock_harness.market_liquidity import (
    analyze_benchmark_volume_fallback,
    analyze_market_liquidity,
)
from stock_harness.models import InstrumentKind, StoredDailyBar
from stock_harness.replay import FrozenSignal
from stock_harness.review_systems import (
    MEAN_REVERSION_SYSTEM_ID,
    MeanReversionReviewSystem,
    analyze_mean_reversion_entity,
)
from stock_harness.review_systems.contracts import AnalysisSystemContext


BATCH_SIZE = 200
LOOKBACK_BARS = 260
REPLAY_VARIANTS = {
    "multistage": MeanReversionPolicyConfig(False, False, False),
    "market": MeanReversionPolicyConfig(True, False, False),
    "relative": MeanReversionPolicyConfig(True, True, False),
    "full": MeanReversionPolicyConfig(True, True, True),
}


class MeanReversionReplayStore(Protocol):
    def list_active_stock_symbols_for_screening(
        self, as_of_date: date | None = None,
    ) -> list[dict[str, str]]: ...

    def search_instruments(self, **kwargs) -> list[dict[str, object]]: ...

    def get_recent_daily_bars_many(
        self, symbols: Sequence[str], end_date: date, limit: int,
    ) -> dict[str, list[StoredDailyBar]]: ...

    def get_recent_causally_adjusted_stock_bars_many(
        self, symbols: Sequence[str], end_date: date, limit: int,
    ) -> tuple[dict[str, list[StoredDailyBar]], dict[str, str]]: ...

    def get_daily_bars(
        self, symbol: str, start_date: date, end_date: date,
    ) -> list[StoredDailyBar]: ...

    def get_adjustment_factors(self, symbol: str, start_date: date, end_date: date): ...

    def get_instrument_kind(self, symbol: str) -> InstrumentKind | None: ...

    def list_trading_dates(
        self, source: str, start_date: date, end_date: date,
    ) -> list[date]: ...

    def calculate_board_breadth_snapshots(self, effective_date: date): ...

    def calculate_board_hotspot_snapshots(self, effective_date: date): ...

    def calculate_board_capacity_snapshots(self, effective_date: date): ...

    def get_market_turnover_proxy(
        self, effective_date: date, sessions: int = 25,
    ) -> list[float]: ...

    def get_market_turnover_range(
        self, start_date: date, end_date: date,
    ) -> list[tuple[date, float]]: ...


def select_replay_dates(
    store: MeanReversionReplayStore, through: date, sessions: int,
) -> list[date]:
    """Prefer actual benchmark sessions and use the provider calendar as fallback."""
    date_start = through - timedelta(days=max(180, sessions * 4))
    dates = [
        bar.trade_date
        for bar in store.get_daily_bars("000001.SH", date_start, through)
    ][-sessions:]
    if len(dates) == sessions:
        return dates
    return store.list_trading_dates("tushare", date_start, through)[-sessions:]


class MeanReversionReplayAdapter:
    """Generate only frozen eligible signals; never write daily-review snapshots."""

    def __init__(
        self, store: MeanReversionReplayStore,
        progress: Callable[[int, int, date, int], None] | None = None,
        *, variant: str = "full",
    ) -> None:
        if variant not in REPLAY_VARIANTS:
            raise ValueError(f"unsupported mean-reversion replay variant: {variant}")
        self._store = store
        self._boards = _list_boards(store)
        self._progress = progress
        self._variant = variant
        self._policy_config = REPLAY_VARIANTS[variant]
        self.diagnostics: list[dict[str, object]] = []

    def generate(self, cutoffs: Sequence[date]):
        self.diagnostics = []
        turnover_by_date = self._store.get_market_turnover_range(
            min(cutoffs) - timedelta(days=60), max(cutoffs),
        ) if cutoffs else []
        for date_index, cutoff in enumerate(cutoffs, 1):
            counters: Counter[str] = Counter()
            entities: dict[str, list[dict[str, object]]] = {
                "market": [], "board": [], "stock": [],
            }
            market = [("000001.SH", "traded"), ("SHAMV.A", "synthetic-not-traded")]
            market_bars = self._store.get_recent_daily_bars_many(
                [item[0] for item in market], cutoff, LOOKBACK_BARS,
            )
            benchmark = market_bars.get("000001.SH", ())
            turnover = [
                value for trade_date, value in turnover_by_date
                if trade_date <= cutoff
            ][-25:]
            liquidity = (
                analyze_market_liquidity(turnover)
                if len(turnover) >= 25
                else analyze_benchmark_volume_fallback(benchmark)
            )
            for symbol, semantics in market:
                entity = _build_entity(
                    symbol, "market", market_bars.get(symbol, ()), cutoff,
                    counters, volume_semantics=semantics,
                )
                if entity is not None:
                    entities["market"].append(entity)

            board_symbols = [str(item["symbol"]) for item in self._boards]
            for page in _pages(board_symbols):
                bars_by_symbol = self._store.get_recent_daily_bars_many(
                    page, cutoff, LOOKBACK_BARS,
                )
                for symbol in page:
                    entity = _build_entity(
                        symbol, "board", bars_by_symbol.get(symbol, ()), cutoff, counters,
                        benchmark_bars=benchmark,
                    )
                    if entity is not None:
                        entities["board"].append(entity)

            stocks = self._store.list_active_stock_symbols_for_screening(cutoff)
            name_by_symbol = {
                str(item["symbol"]): str(item.get("name") or item["symbol"])
                for item in stocks
            }
            stock_symbols = sorted(name_by_symbol)
            for page in _pages(stock_symbols):
                bars_by_symbol, basis_by_symbol = (
                    self._store.get_recent_causally_adjusted_stock_bars_many(
                        page, cutoff, LOOKBACK_BARS,
                    )
                )
                for symbol in page:
                    gates = []
                    if basis_by_symbol.get(symbol) not in {
                        "forward-adjusted-as-of", "raw-continuity-checked",
                    }:
                        gates.append("adjustment-factors-incomplete")
                    if is_risk_name(name_by_symbol[symbol]):
                        gates.append("risk-name")
                    entity = _build_entity(
                        symbol, "stock", bars_by_symbol.get(symbol, ()), cutoff,
                        counters, extra_disqualifiers=gates,
                        benchmark_bars=benchmark,
                    )
                    if entity is not None:
                        entities["stock"].append(entity)
            analyzed_count = sum(
                value for key, value in counters.items() if key.endswith(":analyzed")
            )
            permission = build_market_permission(
                entities["market"],
                candidate_count=len(entities["board"]) + len(entities["stock"]),
                entity_count=analyzed_count,
                liquidity=liquidity,
            )
            needs_board_context = bool(
                self._policy_config.enforce_board_context
                and permission.get("status") != "blocked"
                and entities["board"]
            )
            board_breadth = (
                self._store.calculate_board_breadth_snapshots(cutoff)
                if needs_board_context else {}
            )
            board_hotspots = (
                self._store.calculate_board_hotspot_snapshots(cutoff)
                if needs_board_context else {}
            )
            board_capacities = (
                self._store.calculate_board_capacity_snapshots(cutoff)
                if needs_board_context else {}
            )
            for entity in entities["board"]:
                entity["board_breadth"] = board_breadth.get(
                    str(entity["symbol"]), {}
                )
            results = MeanReversionReviewSystem(
                policy_config=self._policy_config,
                version_suffix=f"experiment-{self._variant}",
            ).analyze(AnalysisSystemContext(
                entities_by_scope=entities,
                dependencies={
                    "mean_reversion_facts": True,
                    "market_liquidity_context": liquidity,
                    "board_capacity_features": board_capacities,
                    "board_hotspot_features": board_hotspots,
                    "mean_reversion_analyzed_entity_count": analyzed_count,
                    "mean_reversion_market_permission": permission,
                },
            ))
            signals = []
            for result in results:
                scope = str(result["entity_scope"])
                family = str(result["setup_family"])
                eligibility = result["eligibility"]
                if not eligibility["eligible"]:
                    for reason in eligibility["rejection_reasons"]:
                        counters[f"{scope}:rejected:{reason}"] += 1
                    continue
                counters[f"{scope}:eligible:{family}"] += 1
                signals.append(_signal_from_result(result, cutoff))
            near_misses = sorted(
                (
                    {
                        "symbol": str(result["symbol"]),
                        "scope": str(result["entity_scope"]),
                        "setup_family": str(result["setup_family"]),
                        "score": float(result["scorecard"]["total_score"]),
                        "rejection_reasons": list(
                            result["eligibility"]["rejection_reasons"]
                        ),
                        "entry_price": result["opportunity"].get("entry_price"),
                        "invalidation_price": result["opportunity"].get(
                            "invalidation_price"
                        ),
                        "selected_target_price": result["opportunity"].get(
                            "selected_target_price"
                        ),
                        "stressed_risk_reward": result["opportunity"].get(
                            "stressed_risk_reward"
                        ),
                    }
                    for result in results
                    if result["entity_scope"] != "market"
                    and not result["eligibility"]["eligible"]
                ),
                key=lambda item: (-item["score"], item["symbol"]),
            )[:10]
            self.diagnostics.append({
                "effective_date": cutoff.isoformat(),
                "counts": dict(sorted(counters.items())),
                "eligible_count": len(signals),
                "variant": self._variant,
                "market_permission": _market_permission_from_results(results),
                "near_misses": near_misses,
            })
            if self._progress is not None:
                self._progress(date_index, len(cutoffs), cutoff, len(signals))
            yield from signals


class SQLiteReplayFutureDataSource:
    """Read post-signal bars in the signal date's causal price basis."""

    def __init__(
        self, store: MeanReversionReplayStore, available_through: date,
    ) -> None:
        self._store = store
        self._through = available_through

    def future_bars(
        self, signal: FrozenSignal, sessions: int | None,
    ) -> Sequence[StoredDailyBar]:
        bars = self._store.get_daily_bars(
            signal.symbol, signal.signal_date + timedelta(days=1), self._through,
        )[:sessions]
        if self._store.get_instrument_kind(signal.symbol) is not InstrumentKind.STOCK:
            return bars
        factors = self._store.get_adjustment_factors(
            signal.symbol, signal.signal_date, self._through,
        )
        by_date = {item.trade_date: item.factor for item in factors}
        anchor = by_date.get(signal.signal_date)
        if anchor is None or any(bar.trade_date not in by_date for bar in bars):
            return bars
        return [StoredDailyBar(
            symbol=bar.symbol, trade_date=bar.trade_date,
            open=bar.open * by_date[bar.trade_date] / anchor,
            high=bar.high * by_date[bar.trade_date] / anchor,
            low=bar.low * by_date[bar.trade_date] / anchor,
            close=bar.close * by_date[bar.trade_date] / anchor,
            volume=bar.volume, source=bar.source, updated_at_ms=bar.updated_at_ms,
        ) for bar in bars]


def _analyze(
    symbol: str,
    scope: str,
    bars: Sequence[StoredDailyBar],
    cutoff: date,
    counters: Counter[str],
    *,
    volume_semantics: str = "traded",
    extra_disqualifiers: Sequence[str] = (),
    benchmark_bars: Sequence[StoredDailyBar] = (),
) -> FrozenSignal | None:
    entity = _build_entity(
        symbol, scope, bars, cutoff, counters,
        volume_semantics=volume_semantics,
        extra_disqualifiers=extra_disqualifiers,
        benchmark_bars=benchmark_bars,
    )
    if entity is None:
        return None
    result = analyze_mean_reversion_entity(scope, entity)
    eligibility = result["eligibility"]
    if not eligibility["eligible"]:
        for reason in eligibility["rejection_reasons"]:
            counters[f"{scope}:rejected:{reason}"] += 1
        return None
    counters[f"{scope}:eligible:{result['setup_family']}"] += 1
    return _signal_from_result(result, cutoff)


def _build_entity(
    symbol: str,
    scope: str,
    bars: Sequence[StoredDailyBar],
    cutoff: date,
    counters: Counter[str],
    *,
    volume_semantics: str = "traded",
    extra_disqualifiers: Sequence[str] = (),
    benchmark_bars: Sequence[StoredDailyBar] = (),
) -> dict[str, object] | None:
    counters[f"{scope}:analyzed"] += 1
    if not bars or bars[-1].trade_date != cutoff:
        counters[f"{scope}:stale-or-missing"] += 1
        return None
    facts = build_mean_reversion_facts(
        bars, volume_semantics=volume_semantics, benchmark_bars=benchmark_bars,
    )
    family = str(facts.get("setup_family") or "none")
    state = str(facts.get("state") or "unqualified")
    counters[f"{scope}:family:{family}"] += 1
    counters[f"{scope}:state:{state}"] += 1
    if scope != "market" and (family == "none" or state != "reversal-confirmed"):
        return None
    facts["disqualifiers"] = sorted({
        *(str(item) for item in facts.get("disqualifiers", [])),
        *(str(item) for item in extra_disqualifiers),
    })
    return {"symbol": symbol, "entity_key": symbol, "mean_reversion": facts}


def _signal_from_result(
    result: dict[str, object], cutoff: date,
) -> FrozenSignal:
    scorecard = result["scorecard"]
    opportunity = result["opportunity"]
    return FrozenSignal(
        system_id=MEAN_REVERSION_SYSTEM_ID,
        system_version=str(result["system_version"]),
        symbol=str(result["symbol"]),
        scope=str(result["entity_scope"]),
        signal_date=cutoff,
        direction="long",
        reference_close=float(opportunity["entry_price"]),
        score=float(scorecard["total_score"]),
        setup_family=str(result["setup_family"]),
        invalidation_price=float(opportunity["invalidation_price"]),
        selected_target_price=float(opportunity["selected_target_price"]),
        metadata={
            "state": opportunity.get("state"),
            "opportunity_tier": opportunity.get("tier"),
            "maximum_holding_sessions": opportunity.get("maximum_holding_sessions"),
            "selected_target_label": opportunity.get("selected_target_label"),
            "stressed_risk_reward": opportunity.get("stressed_risk_reward"),
            "has_stressed_3r_target": (
                opportunity.get("asymmetry", {}).get("has_stressed_3r_target")
                if isinstance(opportunity.get("asymmetry"), dict) else False
            ),
        },
    )


def _list_boards(store: MeanReversionReplayStore) -> list[dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for classification in ("concept", "industry"):
        offset = 0
        while True:
            page = store.search_instruments(
                classification=classification, active=True, limit=500, offset=offset,
            )
            result.update((str(item["symbol"]), item) for item in page)
            if len(page) < 500:
                break
            offset += len(page)
    return [result[symbol] for symbol in sorted(result)]


def _market_permission_from_results(
    results: Sequence[dict[str, object]],
) -> dict[str, object]:
    for result in results:
        payload = result.get("system_payload")
        decision = payload.get("decision") if isinstance(payload, dict) else None
        context = decision.get("context") if isinstance(decision, dict) else None
        permission = context.get("market_permission") if isinstance(context, dict) else None
        if isinstance(permission, dict):
            return permission
    return {}


def _pages(values: Sequence[str]):
    for offset in range(0, len(values), BATCH_SIZE):
        yield values[offset:offset + BATCH_SIZE]
