from dataclasses import replace
from datetime import date

import pytest

from stock_harness.futures_continuous import (
    materialize_continuous,
    materialize_raw_continuous,
)
from stock_harness.models import (
    FuturesBarState,
    FuturesContinuousSeries,
    FuturesDailyBar,
    FuturesExchange,
    FuturesPriceBasis,
    FuturesRollMapping,
    FuturesSeriesKind,
)


SERIES = FuturesContinuousSeries(
    symbol="FUTCONT:SHFE:CU:MAIN:raw",
    provider_symbol="CU.SHF",
    product_symbol="FUTPROD:SHFE:CU",
    display_name="Copper main",
    exchange=FuturesExchange.SHFE,
    series_kind=FuturesSeriesKind.MAIN,
    series_variant="MAIN",
    price_basis=FuturesPriceBasis.RAW,
    rule_version="tushare-fut-mapping-v1",
)
FIRST = "FUT:SHFE:CU:202609"
SECOND = "FUT:SHFE:CU:202610"


def _mapping(day: date, contract: str) -> FuturesRollMapping:
    return FuturesRollMapping(SERIES.symbol, SERIES.provider_symbol, day, contract, contract)


def _bar(symbol: str, day: date, close: float, volume: int, oi: float) -> FuturesDailyBar:
    return FuturesDailyBar(
        symbol=symbol, trading_day=day, provider_date=day,
        open=close - 1, high=close + 2, low=close - 2, close=close,
        previous_close=close - 1, settlement=close + 0.5,
        previous_settlement=close - 0.5, volume_contracts=volume,
        amount=1_000_000, open_interest_contracts=oi,
        open_interest_change_contracts=10, delivery_settlement=None,
        source="tushare-futures", state=FuturesBarState.FINAL,
    )


def test_raw_continuous_maps_exact_contract_and_marks_roll() -> None:
    days = [date(2026, 8, day) for day in range(17, 21)]
    mappings = [_mapping(days[0], FIRST), _mapping(days[2], SECOND)]
    bars = [
        _bar(FIRST, days[0], 100, 1000, 5000),
        _bar(FIRST, days[1], 102, 1100, 5100),
        _bar(FIRST, days[2], 103, 900, 4900),
        _bar(SECOND, days[2], 108, 2100, 8000),
        _bar(SECOND, days[3], 109, 2200, 8100),
    ]

    result = materialize_raw_continuous(SERIES, mappings, bars)

    assert [item.close for item in result.bars] == [100, 102, 108, 109]
    assert [item.mapped_contract_symbol for item in result.bars] == [
        FIRST, FIRST, SECOND, SECOND,
    ]
    assert [item.roll_event for item in result.bars] == [False, False, True, False]
    assert (result.bars[2].volume_contracts, result.bars[2].open_interest_contracts) == (
        2100, 8000,
    )
    assert len(result.rolls) == 1
    assert result.rolls[0].effective_from == days[2]
    assert result.rolls[0].outgoing_contract_symbol == FIRST
    assert result.rolls[0].outgoing_close == 103
    assert result.rolls[0].incoming_close == 108
    assert result.warnings == ()
    assert len(result.input_digest) == 32


def test_raw_continuous_is_deterministic_and_respects_as_of_mapping() -> None:
    first_day, roll_day = date(2026, 8, 19), date(2026, 8, 20)
    mappings = [_mapping(first_day, FIRST), _mapping(roll_day, SECOND)]
    bars = [
        _bar(SECOND, roll_day, 108, 200, 300),
        _bar(FIRST, roll_day, 102, 100, 150),
        _bar(FIRST, first_day, 101, 90, 140),
    ]
    left = materialize_raw_continuous(SERIES, mappings, bars)
    right = materialize_raw_continuous(SERIES, list(reversed(mappings)), list(reversed(bars)))
    assert left == right
    assert [item.close for item in left.bars] == [101, 108]


def test_raw_continuous_reports_missing_mapped_bar_without_substitution() -> None:
    first_day, roll_day = date(2026, 8, 19), date(2026, 8, 20)
    result = materialize_raw_continuous(
        SERIES,
        [_mapping(first_day, FIRST), _mapping(roll_day, SECOND)],
        [_bar(FIRST, first_day, 101, 90, 140), _bar(FIRST, roll_day, 102, 100, 150)],
    )
    assert len(result.bars) == 1
    assert result.warnings[0].code == "missing-mapped-contract-bar"
    assert result.warnings[0].contract_symbol == SECOND


def test_backward_ratio_keeps_latest_prices_and_unadjusted_activity_fields() -> None:
    days = [date(2026, 8, day) for day in range(18, 21)]
    adjusted = replace(
        SERIES,
        symbol="FUTCONT:SHFE:CU:MAIN:backward-ratio",
        price_basis=FuturesPriceBasis.BACKWARD_RATIO,
        rule_version="backward-ratio-v1",
    )
    mappings = [
        replace(_mapping(days[0], FIRST), series_symbol=adjusted.symbol),
        replace(_mapping(days[2], SECOND), series_symbol=adjusted.symbol),
    ]
    bars = [
        _bar(FIRST, days[0], 90, 1000, 5000),
        _bar(FIRST, days[1], 100, 1100, 5100),
        _bar(FIRST, days[2], 100, 900, 4900),
        _bar(SECOND, days[2], 110, 2100, 8000),
    ]

    result = materialize_continuous(adjusted, mappings, bars)

    assert [item.close for item in result.bars] == pytest.approx([99, 110, 110])
    assert result.bars[-1].close == 110
    assert [item.volume_contracts for item in result.bars] == [1000, 1100, 2100]
    assert [item.open_interest_contracts for item in result.bars] == [5000, 5100, 8000]
    assert [item.amount for item in result.bars] == [1_000_000] * 3
    assert result.rolls[0].adjustment_factor == pytest.approx(1.1)
    assert result.rolls[0].outgoing_close == 100
    assert result.rolls[0].incoming_close == 110


def test_backward_ratio_fails_closed_without_roll_overlap() -> None:
    first_day, roll_day = date(2026, 8, 19), date(2026, 8, 20)
    adjusted = replace(
        SERIES,
        symbol="FUTCONT:SHFE:CU:MAIN:backward-ratio",
        price_basis=FuturesPriceBasis.BACKWARD_RATIO,
    )
    mappings = [
        replace(_mapping(first_day, FIRST), series_symbol=adjusted.symbol),
        replace(_mapping(roll_day, SECOND), series_symbol=adjusted.symbol),
    ]
    with pytest.raises(ValueError, match="both contracts on roll date"):
        materialize_continuous(
            adjusted, mappings,
            [_bar(FIRST, first_day, 100, 1, 1), _bar(SECOND, roll_day, 110, 1, 1)],
        )


def test_continuous_rejects_non_final_or_unsupported_basis() -> None:
    day = date(2026, 8, 20)
    with pytest.raises(ValueError, match="canonical final"):
        materialize_raw_continuous(
            SERIES, [_mapping(day, FIRST)],
            [replace(_bar(FIRST, day, 100, 1, 1), state=FuturesBarState.PROVISIONAL)],
        )
    with pytest.raises(ValueError, match="unsupported continuous price basis"):
        materialize_continuous(
            replace(SERIES, price_basis=FuturesPriceBasis.BACKWARD_ADDITIVE), [], []
        )
