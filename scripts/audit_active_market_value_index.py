"""Audit the materialized StockHarness active-market-value index."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sqlite3

from stock_harness.active_market_value import DEFAULT_DEFINITION_ID, DEFAULT_SYMBOL
from stock_harness.config import load_runtime_settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-config", type=Path, default=Path("config/providers.local.yaml"))
    parser.add_argument("--storage-config", type=Path, default=Path("config/storage.local.yaml"))
    parser.add_argument("--minimum-coverage", type=float, default=0.95)
    args = parser.parse_args()
    if not 0 <= args.minimum_coverage <= 1:
        raise SystemExit("--minimum-coverage must be between 0 and 1")

    settings = load_runtime_settings(args.provider_config, args.storage_config)
    database = settings.database_path.resolve()
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        definition = connection.execute(
            """
            SELECT definition.status, definition.algorithm_version,
                   definition.base_date, definition.base_value, instrument.instrument_id
            FROM active_market_value_definitions AS definition
            JOIN instruments AS instrument USING (instrument_id)
            WHERE definition.definition_id = ? AND instrument.symbol = ?
            """,
            (DEFAULT_DEFINITION_ID, DEFAULT_SYMBOL),
        ).fetchone()
        aggregate = connection.execute(
            """
            SELECT count(*) AS rows, min(trade_date) AS first_date,
                   max(trade_date) AS last_date, min(coverage_ratio) AS min_coverage,
                   avg(coverage_ratio) AS avg_coverage,
                   sum(coverage_ratio < ?) AS low_coverage_dates,
                   sum(coverage_ratio < 0 OR coverage_ratio > 1) AS invalid_coverage,
                   sum(absolute_high < max(absolute_open, absolute_close)
                       OR absolute_low > min(absolute_open, absolute_close)
                       OR high < max(open, close) OR low > min(open, close)) AS invalid_envelopes,
                   sum(eligible_count > total_count OR eligible_count <= 0) AS invalid_counts
            FROM active_market_value_daily_bars
            WHERE definition_id = ?
            """,
            (args.minimum_coverage, DEFAULT_DEFINITION_ID),
        ).fetchone()
        mirror = connection.execute(
            """
            SELECT count(*) AS rows,
                   sum(abs(chart.open - active.open) > 1e-8
                       OR abs(chart.high - active.high) > 1e-8
                       OR abs(chart.low - active.low) > 1e-8
                       OR abs(chart.close - active.close) > 1e-8) AS mismatches
            FROM active_market_value_daily_bars AS active
            JOIN daily_bars AS chart
              ON chart.instrument_id = ? AND chart.trade_date = active.trade_date
            WHERE active.definition_id = ?
            """,
            (int(definition[4]) if definition else -1, DEFAULT_DEFINITION_ID),
        ).fetchone()
        contribution_audit = connection.execute(
            """
            WITH changes AS (
                SELECT trade_date, absolute_close, contribution_total, input_digest,
                       lag(absolute_close) OVER (ORDER BY trade_date) AS previous_close
                FROM active_market_value_daily_bars
                WHERE definition_id = ?
            )
            SELECT sum(input_digest = '') AS missing_input_digests,
                   sum(previous_close IS NOT NULL AND
                       abs(contribution_total - (absolute_close - previous_close)) >
                       max(1.0, abs(absolute_close - previous_close) * 1e-10)
                   ) AS contribution_mismatches
            FROM changes
            """,
            (DEFAULT_DEFINITION_ID,),
        ).fetchone()
        contribution_rows = connection.execute(
            """
            SELECT count(*) AS rows, count(DISTINCT trade_date) AS dates,
                   sum((direction = 'positive' AND change_contribution <= 0)
                       OR (direction = 'negative' AND change_contribution >= 0)) AS invalid_signs,
                   max(contribution_rank) AS maximum_rank,
                   max(trade_date) AS last_date
            FROM active_market_value_daily_contributions
            WHERE definition_id = ?
            """,
            (DEFAULT_DEFINITION_ID,),
        ).fetchone()
    finally:
        connection.close()

    def iso_date(value: int | None) -> str | None:
        if value is None:
            return None
        raw = str(value)
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:])).isoformat()

    problems: list[str] = []
    if definition is None:
        problems.append("definition_missing")
    elif definition["status"] != "ready":
        problems.append(f"definition_status_{definition['status']}")
    if aggregate["rows"] == 0:
        problems.append("daily_bars_missing")
    if aggregate["invalid_coverage"]:
        problems.append("coverage_out_of_range")
    if aggregate["invalid_envelopes"]:
        problems.append("invalid_ohlc_envelopes")
    if aggregate["invalid_counts"]:
        problems.append("invalid_constituent_counts")
    if mirror["rows"] != aggregate["rows"] or mirror["mismatches"]:
        problems.append("chart_materialization_mismatch")
    if contribution_audit["missing_input_digests"]:
        problems.append("input_digest_missing")
    if contribution_audit["contribution_mismatches"]:
        problems.append("contribution_total_mismatch")
    if contribution_rows["rows"] == 0:
        problems.append("contributions_missing")
    if contribution_rows["invalid_signs"]:
        problems.append("contribution_direction_invalid")
    if contribution_rows["maximum_rank"] is not None and contribution_rows["maximum_rank"] > 10:
        problems.append("contribution_rank_invalid")
    if contribution_rows["last_date"] != aggregate["last_date"]:
        problems.append("latest_contributions_missing")

    report = {
        "status": "pass" if not problems else "fail",
        "database": str(database),
        "definition_id": DEFAULT_DEFINITION_ID,
        "symbol": DEFAULT_SYMBOL,
        "algorithm_version": definition["algorithm_version"] if definition else None,
        "base_date": iso_date(definition["base_date"]) if definition else None,
        "base_value": definition["base_value"] if definition else None,
        "rows": aggregate["rows"],
        "first_trade_date": iso_date(aggregate["first_date"]),
        "last_trade_date": iso_date(aggregate["last_date"]),
        "minimum_coverage_ratio": aggregate["min_coverage"],
        "average_coverage_ratio": aggregate["avg_coverage"],
        "coverage_warning_threshold": args.minimum_coverage,
        "low_coverage_dates": aggregate["low_coverage_dates"],
        "invalid_coverage_dates": aggregate["invalid_coverage"],
        "invalid_envelope_dates": aggregate["invalid_envelopes"],
        "invalid_count_dates": aggregate["invalid_counts"],
        "chart_rows": mirror["rows"],
        "chart_mismatches": mirror["mismatches"],
        "missing_input_digests": contribution_audit["missing_input_digests"],
        "contribution_total_mismatches": contribution_audit["contribution_mismatches"],
        "contribution_rows": contribution_rows["rows"],
        "contribution_dates": contribution_rows["dates"],
        "latest_contribution_date": iso_date(contribution_rows["last_date"]),
        "invalid_contribution_signs": contribution_rows["invalid_signs"],
        "maximum_contribution_rank": contribution_rows["maximum_rank"],
        "problems": problems,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if problems:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
