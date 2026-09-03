from stock_harness.sqlite_custom_group_store import SQLiteCustomGroupStoreMixin
from stock_harness.sqlite_etf_holding_store import SQLiteEtfHoldingStoreMixin
from stock_harness.sqlite_provider_quality_store import SQLiteProviderQualityStoreMixin
from stock_harness.sqlite_store import SQLiteMarketDataStore


def test_domain_store_methods_are_exposed_by_the_public_facade() -> None:
    assert (
        SQLiteMarketDataStore.list_custom_groups
        is SQLiteCustomGroupStoreMixin.list_custom_groups
    )
    assert (
        SQLiteMarketDataStore.list_etf_holdings
        is SQLiteEtfHoldingStoreMixin.list_etf_holdings
    )
    assert (
        SQLiteMarketDataStore.list_provider_incidents
        is SQLiteProviderQualityStoreMixin.list_provider_incidents
    )
    assert (
        SQLiteMarketDataStore.list_repair_jobs
        is SQLiteProviderQualityStoreMixin.list_repair_jobs
    )
