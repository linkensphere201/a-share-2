"""Deterministic materialization of futures continuous daily series."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from hashlib import sha256
import json

from stock_harness.models import (
    FuturesBarState,
    FuturesContinuousSeries,
    FuturesDailyBar,
    FuturesPriceBasis,
    FuturesRollMapping,
)


@dataclass(frozen=True, slots=True)
class FuturesContinuousWarning:
    code: str
    trading_day: date
    contract_symbol: str | None
    message: str


@dataclass(frozen=True, slots=True)
class FuturesContinuousRoll:
    effective_from: date
    first_output_day: date
    outgoing_contract_symbol: str
    incoming_contract_symbol: str
    outgoing_close: float | None = None
    incoming_close: float | None = None
    adjustment_factor: float | None = None
    adjustment_offset: float | None = None


@dataclass(frozen=True, slots=True)
class FuturesContinuousBuild:
    series_symbol: str
    price_basis: FuturesPriceBasis
    rule_version: str
    bars: tuple[FuturesDailyBar, ...]
    rolls: tuple[FuturesContinuousRoll, ...]
    warnings: tuple[FuturesContinuousWarning, ...]
    input_digest: bytes


def materialize_raw_continuous(
    series: FuturesContinuousSeries,
    mappings: Sequence[FuturesRollMapping],
    contract_bars: Sequence[FuturesDailyBar],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> FuturesContinuousBuild:
    """Map canonical real-contract bars into one unadjusted continuous series."""
    if series.price_basis is not FuturesPriceBasis.RAW:
        raise ValueError("raw materialization requires a raw continuous series")
    return materialize_continuous(
        series, mappings, contract_bars, start_date=start_date, end_date=end_date
    )


def materialize_continuous(
    series: FuturesContinuousSeries,
    mappings: Sequence[FuturesRollMapping],
    contract_bars: Sequence[FuturesDailyBar],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> FuturesContinuousBuild:
    """Materialize raw or backward-ratio continuous history from canonical inputs."""
    series.validate()
    if series.price_basis not in {
        FuturesPriceBasis.RAW, FuturesPriceBasis.BACKWARD_RATIO,
    }:
        raise ValueError(f"unsupported continuous price basis: {series.price_basis}")
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("continuous build start must not exceed end")

    ordered_mappings = sorted(mappings, key=lambda item: item.effective_from)
    for item in ordered_mappings:
        item.validate()
        if item.series_symbol != series.symbol:
            raise ValueError("continuous mapping belongs to a different series")
    if len({item.effective_from for item in ordered_mappings}) != len(ordered_mappings):
        raise ValueError("continuous mappings contain duplicate effective dates")

    mapped_symbols = {item.contract_symbol for item in ordered_mappings}
    bars_by_identity: dict[tuple[str, date], FuturesDailyBar] = {}
    for item in contract_bars:
        if item.state is not FuturesBarState.FINAL:
            raise ValueError("continuous history accepts canonical final bars only")
        item.validate()
        if item.symbol not in mapped_symbols:
            continue
        identity = (item.symbol, item.trading_day)
        if identity in bars_by_identity:
            raise ValueError("continuous input contains duplicate contract/date bars")
        bars_by_identity[identity] = item

    effective_dates = [item.effective_from for item in ordered_mappings]
    candidate_days = sorted({day for _, day in bars_by_identity})
    output: list[FuturesDailyBar] = []
    rolls: list[FuturesContinuousRoll] = []
    warnings: list[FuturesContinuousWarning] = []
    prior_contract: str | None = None

    for trading_day in candidate_days:
        if end_date is not None and trading_day > end_date:
            continue
        mapping_index = bisect_right(effective_dates, trading_day) - 1
        if mapping_index < 0:
            if start_date is not None and trading_day < start_date:
                continue
            warnings.append(FuturesContinuousWarning(
                code="missing-effective-mapping",
                trading_day=trading_day,
                contract_symbol=None,
                message="no mapping was effective on this trading day",
            ))
            continue
        mapping = ordered_mappings[mapping_index]
        source_bar = bars_by_identity.get((mapping.contract_symbol, trading_day))
        if source_bar is None:
            if start_date is not None and trading_day < start_date:
                continue
            warnings.append(FuturesContinuousWarning(
                code="missing-mapped-contract-bar",
                trading_day=trading_day,
                contract_symbol=mapping.contract_symbol,
                message="the mapped real contract has no canonical bar",
            ))
            continue

        if start_date is not None and trading_day < start_date:
            prior_contract = mapping.contract_symbol
            continue

        roll_event = prior_contract is not None and prior_contract != mapping.contract_symbol
        output.append(replace(
            source_bar,
            symbol=series.symbol,
            mapped_contract_symbol=mapping.contract_symbol,
            roll_event=roll_event,
        ))
        if roll_event:
            outgoing_overlap = bars_by_identity.get(
                (prior_contract, mapping.effective_from)
            )
            incoming_overlap = bars_by_identity.get(
                (mapping.contract_symbol, mapping.effective_from)
            )
            if outgoing_overlap is None or incoming_overlap is None:
                warnings.append(FuturesContinuousWarning(
                    code="missing-roll-overlap",
                    trading_day=mapping.effective_from,
                    contract_symbol=mapping.contract_symbol,
                    message="roll evidence lacks same-day closes for both contracts",
                ))
            rolls.append(FuturesContinuousRoll(
                effective_from=mapping.effective_from,
                first_output_day=trading_day,
                outgoing_contract_symbol=prior_contract,
                incoming_contract_symbol=mapping.contract_symbol,
                outgoing_close=(
                    outgoing_overlap.close if outgoing_overlap is not None else None
                ),
                incoming_close=(
                    incoming_overlap.close if incoming_overlap is not None else None
                ),
            ))
        prior_contract = mapping.contract_symbol

    raw_build = FuturesContinuousBuild(
        series_symbol=series.symbol,
        price_basis=series.price_basis,
        rule_version=series.rule_version,
        bars=tuple(output),
        rolls=tuple(rolls),
        warnings=tuple(warnings),
        input_digest=_input_digest(series, ordered_mappings, bars_by_identity.values()),
    )
    if series.price_basis is FuturesPriceBasis.RAW:
        return raw_build
    return _apply_backward_ratio(raw_build)


def _apply_backward_ratio(
    raw_build: FuturesContinuousBuild,
) -> FuturesContinuousBuild:
    adjusted_rolls: list[FuturesContinuousRoll] = []
    for roll in raw_build.rolls:
        if roll.outgoing_close is None or roll.incoming_close is None:
            raise ValueError(
                "backward-ratio adjustment requires both contracts on roll date "
                f"{roll.effective_from.isoformat()}"
            )
        if roll.outgoing_close <= 0 or roll.incoming_close <= 0:
            raise ValueError("backward-ratio adjustment requires positive roll closes")
        adjusted_rolls.append(replace(
            roll,
            adjustment_factor=roll.incoming_close / roll.outgoing_close,
            adjustment_offset=None,
        ))

    adjusted_bars: list[FuturesDailyBar] = []
    for bar in raw_build.bars:
        factor = 1.0
        for roll in adjusted_rolls:
            if bar.trading_day < roll.effective_from:
                if roll.adjustment_factor is None:
                    raise RuntimeError("continuous roll adjustment factor is missing")
                factor *= roll.adjustment_factor
        adjusted_bars.append(_scale_price_fields(bar, factor))
    return replace(raw_build, bars=tuple(adjusted_bars), rolls=tuple(adjusted_rolls))


def _scale_price_fields(bar: FuturesDailyBar, factor: float) -> FuturesDailyBar:
    def scaled(value: float | None) -> float | None:
        return value * factor if value is not None else None

    return replace(
        bar,
        open=bar.open * factor,
        high=bar.high * factor,
        low=bar.low * factor,
        close=bar.close * factor,
        previous_close=scaled(bar.previous_close),
        settlement=scaled(bar.settlement),
        previous_settlement=scaled(bar.previous_settlement),
        delivery_settlement=scaled(bar.delivery_settlement),
    )


def _input_digest(
    series: FuturesContinuousSeries,
    mappings: Sequence[FuturesRollMapping],
    bars: Sequence[FuturesDailyBar],
) -> bytes:
    payload = {
        "series": series.symbol,
        "price_basis": series.price_basis.value,
        "rule_version": series.rule_version,
        "mappings": [
            [item.effective_from.isoformat(), item.contract_symbol]
            for item in mappings
        ],
        "bars": [
            [
                item.symbol, item.trading_day.isoformat(), item.provider_date.isoformat(),
                item.open, item.high, item.low, item.close, item.previous_close,
                item.settlement, item.previous_settlement, item.volume_contracts,
                item.amount, item.open_interest_contracts,
                item.open_interest_change_contracts, item.delivery_settlement,
                item.source,
            ]
            for item in sorted(bars, key=lambda value: (value.trading_day, value.symbol))
        ],
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).digest()
