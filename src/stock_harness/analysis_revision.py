"""Durable analysis dependency version, updated atomically with source writes."""
DEPENDENCIES = (
    "sources", "source_profiles", "instruments", "instrument_catalog_entries", "instrument_aliases",
    "daily_bars", "stock_daily_limits", "stock_adjustment_factors", "stock_trade_status",
    "trading_calendar", "board_memberships", "instrument_mappings", "market_snapshots",
    "custom_instrument_groups", "custom_instrument_group_members", "instrument_tags", "instrument_board_tags",
    "etf_holdings", "etf_holding_receipts", "intraday_daily_bars",
    "custom_indices", "custom_index_revisions", "custom_index_revision_members", "custom_index_daily_bars",
    "active_market_value_definitions", "active_market_value_daily_bars", "active_market_value_features",
    "futures_products", "futures_contracts", "futures_continuous_series", "futures_daily_bars",
    "futures_exchange_calendar", "futures_roll_mappings", "futures_provisional_daily_bars",
    "futures_continuous_roll_events", "futures_continuous_builds",
)


def install_analysis_revision(connection, writer_lock):
    with writer_lock:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS analysis_dependency_revision "
                               "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL)")
            connection.execute("INSERT OR IGNORE INTO analysis_dependency_revision VALUES (1, 0)")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in DEPENDENCIES:
                if table not in tables:
                    continue
                for operation in ("INSERT", "UPDATE", "DELETE"):
                    connection.execute(f"CREATE TRIGGER IF NOT EXISTS analysis_revision_{table}_{operation} "
                                       f"AFTER {operation} ON {table} BEGIN "
                                       "UPDATE analysis_dependency_revision SET revision=revision+1 WHERE singleton=1; END")
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
