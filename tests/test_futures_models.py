from datetime import date, datetime, timezone

import pytest

from stock_harness.models import (
    FuturesBarState,
    FuturesChangeBasis,
    FuturesContinuousSeries,
    FuturesContract,
    FuturesDailyBar,
    FuturesExchange,
    FuturesLifecycleStatus,
    FuturesPriceBasis,
    FuturesProduct,
    FuturesSeriesKind,
    FuturesSessionPhase,
    FuturesTradingDayOwnership,
    InstrumentKind,
    canonical_futures_continuous_symbol,
    canonical_futures_contract_symbol,
    canonical_futures_product_symbol,
)


def test_existing_instrument_kind_values_remain_stable() -> None:
    assert [kind.value for kind in InstrumentKind][:5] == [
        "stock", "etf", "index", "sector", "custom-index",
    ]
    assert InstrumentKind.FUTURES_PRODUCT.value == "futures-product"
    assert InstrumentKind.FUTURES_CONTRACT.value == "futures-contract"
    assert InstrumentKind.FUTURES_CONTINUOUS.value == "futures-continuous"


def test_canonical_futures_identities_separate_product_contract_and_series() -> None:
    assert canonical_futures_product_symbol(FuturesExchange.SHFE, "cu") == "FUTPROD:SHFE:CU"
    assert canonical_futures_contract_symbol(
        FuturesExchange.SHFE, "cu", "202609",
    ) == "FUT:SHFE:CU:202609"
    assert canonical_futures_contract_symbol(
        FuturesExchange.CZCE, "cf", "202601",
    ) == "FUT:CZCE:CF:202601"
    assert canonical_futures_continuous_symbol(
        FuturesExchange.SHFE,
        "cu",
        "MAIN",
        FuturesPriceBasis.RAW,
    ) == "FUTCONT:SHFE:CU:MAIN:raw"
    assert canonical_futures_product_symbol(FuturesExchange.DCE, "l_f") == "FUTPROD:DCE:L_F"
    with pytest.raises(ValueError, match="unsupported characters"):
        canonical_futures_contract_symbol(FuturesExchange.SHFE, "cu/", "202609")
    with pytest.raises(ValueError, match="YYYYMM"):
        canonical_futures_contract_symbol(FuturesExchange.SHFE, "cu", "2609")


def test_product_contract_and_continuous_metadata_validate() -> None:
    product_symbol = canonical_futures_product_symbol(FuturesExchange.SHFE, "CU")
    FuturesProduct(
        product_symbol, "CU", "Copper", FuturesExchange.SHFE, None, 5, "contract", "CNY/tonne",
    ).validate()
    FuturesContract(
        symbol=canonical_futures_contract_symbol(FuturesExchange.SHFE, "CU", "202609"),
        provider_symbol="CU2609.SHF",
        product_symbol=product_symbol,
        display_name="Copper 2609",
        exchange=FuturesExchange.SHFE,
        contract_month="202609",
        listed_on=date(2025, 9, 16),
        last_trading_date=date(2026, 9, 15),
        delivery_date=date(2026, 9, 18),
        multiplier=None,
        per_unit=5,
        trading_unit="contract",
        quote_unit="CNY/tonne",
        lifecycle_status=FuturesLifecycleStatus.TRADING,
    ).validate()
    FuturesContinuousSeries(
        symbol=canonical_futures_continuous_symbol(
            FuturesExchange.SHFE, "CU", "MAIN", FuturesPriceBasis.RAW,
        ),
        provider_symbol="CU.SHF",
        product_symbol=product_symbol,
        display_name="Copper main raw",
        exchange=FuturesExchange.SHFE,
        series_kind=FuturesSeriesKind.MAIN,
        series_variant="MAIN",
        price_basis=FuturesPriceBasis.RAW,
        rule_version="mapping-v1",
    ).validate()


def test_contract_rejects_invalid_lifecycle_dates() -> None:
    contract = FuturesContract(
        symbol="FUT:DCE:M:202609",
        provider_symbol="M2609.DCE",
        product_symbol="FUTPROD:DCE:M",
        display_name="Soybean meal 2609",
        exchange=FuturesExchange.DCE,
        contract_month="202609",
        listed_on=date(2026, 1, 1),
        last_trading_date=date(2025, 12, 31),
        delivery_date=None,
        multiplier=None,
        per_unit=10,
        trading_unit="contract",
        quote_unit="CNY/tonne",
        lifecycle_status=FuturesLifecycleStatus.EXPIRED,
    )
    with pytest.raises(ValueError, match="last trading date"):
        contract.validate()


def test_night_session_uses_the_later_futures_trading_day() -> None:
    ownership = FuturesTradingDayOwnership(
        trading_day=date(2026, 8, 24),
        calendar_date=date(2026, 8, 21),
        provider_date=date(2026, 8, 24),
        exchange=FuturesExchange.SHFE,
        session_phase=FuturesSessionPhase.NIGHT,
    )
    ownership.validate()
    with pytest.raises(ValueError, match="must precede trading day"):
        FuturesTradingDayOwnership(
            trading_day=date(2026, 8, 24),
            calendar_date=date(2026, 8, 24),
            provider_date=date(2026, 8, 24),
            exchange=FuturesExchange.SHFE,
            session_phase=FuturesSessionPhase.NIGHT,
        ).validate()


def test_futures_change_defaults_to_previous_settlement_not_candle_direction() -> None:
    bar = _bar(open_price=102, close=101, previous_close=98, previous_settlement=100)
    bar.validate()
    assert bar.candle_direction == -1
    assert bar.change_percent() == pytest.approx(1.0)
    assert bar.change_percent(FuturesChangeBasis.PREVIOUS_CLOSE) == pytest.approx(3.06122449)
    assert bar.to_chart_bar().trade_date == date(2026, 8, 20)


def test_provisional_bar_requires_provider_observation_time() -> None:
    with pytest.raises(ValueError, match="requires provider time"):
        _bar(state=FuturesBarState.PROVISIONAL, provider_time=None).validate()
    _bar(
        state=FuturesBarState.PROVISIONAL,
        provider_time=datetime(2026, 8, 20, 14, 30, tzinfo=timezone.utc),
    ).validate()


def _bar(
    *,
    open_price: float = 100,
    close: float = 101,
    previous_close: float | None = 99,
    previous_settlement: float | None = 100,
    state: FuturesBarState = FuturesBarState.FINAL,
    provider_time: datetime | None = None,
) -> FuturesDailyBar:
    return FuturesDailyBar(
        symbol="FUT:SHFE:CU:202609",
        trading_day=date(2026, 8, 20),
        provider_date=date(2026, 8, 20),
        open=open_price,
        high=103,
        low=97,
        close=close,
        previous_close=previous_close,
        settlement=100.5,
        previous_settlement=previous_settlement,
        volume_contracts=12345,
        amount=1_234_500,
        open_interest_contracts=54321,
        open_interest_change_contracts=-123,
        delivery_settlement=None,
        source="tushare" if state is FuturesBarState.FINAL else "akshare",
        state=state,
        provider_time=provider_time,
    )
