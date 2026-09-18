"""Ordered schema initialization; never run by read-only worker connections."""

from stock_harness.sqlite_schema import SCHEMA


def initialize_database(store):
    with store._writer_lock:
        store._connection.executescript(SCHEMA)
    store._ensure_chat_policy_version()
    store._ensure_chat_conversation_sessions()
    store._ensure_chat_typed_contexts()
    store._ensure_chat_workspace_contexts()
    store._ensure_chat_context_index()
    store._ensure_chat_template_version()
    store._futures_storage_ready = False
    store._futures_storage_error: str | None = None
    store._ensure_futures_schema()
    store._ensure_custom_index_volume()
    store._ensure_custom_group_member_roles()
    store._ensure_market_snapshot_metrics()
    store._ensure_generated_analysis_target_settings()
    store._ensure_generated_analysis_scenario_type()
    store._ensure_screener_candidate_states()
    store._ensure_active_market_value_diagnostics()
    store._ensure_signal_observation_columns()
    store.ensure_board_theme_registry()
    store._backfill_pinyin_aliases()
