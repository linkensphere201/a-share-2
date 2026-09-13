"""Fast, non-persisting mean-reversion adapter for the shared replay engine."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Callable, Protocol

from stock_harness.board_leader_scan import is_risk_name
from stock_harness.mean_reversion_facts import build_mean_reversion_facts
from stock_harness.models import InstrumentKind, StoredDailyBar
from stock_harness.replay import FrozenSignal
from stock_harness.review_systems import (
    MEAN_REVERSION_SYSTEM_ID,
    MEAN_REVERSION_VERSION,
    analyze_mean_reversion_entity,
)


BATCH_SIZE = 200
LOOKBACK_BARS = 260


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
    ) -> None:
        self._store = store
        self._boards = _list_boards(store)
        self._progress = progress
        self.diagnostics: list[dict[str, object]] = []

    def generate(self, cutoffs: Sequence[date]):
        self.diagnostics = []
        for date_index, cutoff in enumerate(cutoffs, 1):
            counters: Counter[str] = Counter()
            signals = []
            market = [("000001.SH", "traded"), ("SHAMV.A", "synthetic-not-traded")]
            market_bars = self._store.get_recent_daily_bars_many(
                [item[0] for item in market], cutoff, LOOKBACK_BARS,
            )
            for symbol, semantics in market:
                signal = _analyze(
                    symbol, "market", market_bars.get(symbol, ()), cutoff,
                    counters, volume_semantics=semantics,
                )
                if signal is not None:
                    signals.append(signal)

            board_symbols = [str(item["symbol"]) for item in self._boards]
            for page in _pages(board_symbols):
                bars_by_symbol = self._store.get_recent_daily_bars_many(
                    page, cutoff, LOOKBACK_BARS,
                )
                for symbol in page:
                    signal = _analyze(
                        symbol, "board", bars_by_symbol.get(symbol, ()), cutoff, counters,
                    )
                    if signal is not None:
                        signals.append(signal)

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
                    signal = _analyze(
                        symbol, "stock", bars_by_symbol.get(symbol, ()), cutoff,
                        counters, extra_disqualifiers=gates,
                    )
                    if signal is not None:
                        signals.append(signal)
            self.diagnostics.append({
                "effective_date": cutoff.isoformat(),
                "counts": dict(sorted(counters.items())),
                "eligible_count": len(signals),
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
        self, signal: FrozenSignal, sessions: int,
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
) -> FrozenSignal | None:
    counters[f"{scope}:analyzed"] += 1
    if not bars or bars[-1].trade_date != cutoff:
        counters[f"{scope}:stale-or-missing"] += 1
        return None
    facts = build_mean_reversion_facts(bars, volume_semantics=volume_semantics)
    family = str(facts.get("setup_family") or "none")
    state = str(facts.get("state") or "unqualified")
    counters[f"{scope}:family:{family}"] += 1
    counters[f"{scope}:state:{state}"] += 1
    if family == "none" or state != "reversal-confirmed":
        return None
    facts["disqualifiers"] = sorted({
        *(str(item) for item in facts.get("disqualifiers", [])),
        *(str(item) for item in extra_disqualifiers),
    })
    result = analyze_mean_reversion_entity(scope, {
        "symbol": symbol, "entity_key": symbol, "mean_reversion": facts,
    })
    eligibility = result["eligibility"]
    scorecard = result["scorecard"]
    if not eligibility["eligible"]:
        for reason in eligibility["rejection_reasons"]:
            counters[f"{scope}:rejected:{reason}"] += 1
        return None
    opportunity = result["opportunity"]
    counters[f"{scope}:eligible:{family}"] += 1
    return FrozenSignal(
        system_id=MEAN_REVERSION_SYSTEM_ID,
        system_version=MEAN_REVERSION_VERSION,
        symbol=symbol,
        scope=scope,
        signal_date=cutoff,
        direction="long",
        reference_close=float(opportunity["entry_price"]),
        score=float(scorecard["total_score"]),
        setup_family=family,
        invalidation_price=float(opportunity["invalidation_price"]),
        selected_target_price=float(opportunity["selected_target_price"]),
        metadata={
            "state": state,
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


def _pages(values: Sequence[str]):
    for offset in range(0, len(values), BATCH_SIZE):
        yield values[offset:offset + BATCH_SIZE]
