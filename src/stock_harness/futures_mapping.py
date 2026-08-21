"""Pure normalization and identity checks for futures roll mappings."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from stock_harness.models import FuturesRollMapping


@dataclass(frozen=True, slots=True)
class FuturesContractMappingIdentity:
    provider_symbol: str
    listed_on: date
    last_trading_date: date


def normalize_futures_mappings(
    mappings: Sequence[FuturesRollMapping],
) -> tuple[FuturesRollMapping, ...]:
    return tuple(sorted(
        mappings,
        key=lambda item: (
            item.series_symbol, item.effective_from, item.contract_symbol,
        ),
    ))


def validate_futures_mapping_identities(
    mappings: Sequence[FuturesRollMapping],
    series_provider_symbols: Mapping[str, str],
    contracts: Mapping[str, FuturesContractMappingIdentity],
) -> None:
    if any(
        series_provider_symbols.get(item.series_symbol)
        != item.series_provider_symbol
        or contracts.get(item.contract_symbol) is None
        or contracts[item.contract_symbol].provider_symbol
        != item.contract_provider_symbol
        for item in mappings
    ):
        raise ValueError("futures mapping Provider identity mismatch")
    if any(
        not contracts[item.contract_symbol].listed_on
        <= item.effective_from
        <= contracts[item.contract_symbol].last_trading_date
        for item in mappings
    ):
        raise ValueError("futures mapping date exceeds contract lifecycle")
