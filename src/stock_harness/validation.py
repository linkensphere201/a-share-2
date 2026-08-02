"""Cross-provider comparison without modifying canonical market bars."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from math import isclose

from stock_harness.ports import DailyBarValidationProvider
from stock_harness.sqlite_store import SQLiteMarketDataStore


@dataclass(frozen=True, slots=True)
class ValidationSummary:
    checked: int
    matched: int
    mismatched: int
    missing: int
    errors: int


def validate_symbols(
    store: SQLiteMarketDataStore,
    primary_source: str,
    providers: Sequence[DailyBarValidationProvider],
    symbols: Sequence[str],
    trade_date: date,
    price_abs_tolerance: float = 0.001,
    volume_rel_tolerance: float = 0.001,
    incident_id: int | None = None,
) -> ValidationSummary:
    counts = {"match": 0, "mismatch": 0, "missing": 0, "error": 0}
    for symbol in symbols:
        primary_rows = store.get_daily_bars(symbol, trade_date, trade_date)
        primary = primary_rows[0] if primary_rows else None
        for provider in providers:
            try:
                validator_rows = provider.fetch_symbol_daily_bars(symbol, trade_date, trade_date)
                validator = validator_rows[0] if validator_rows else None
                if primary is None or validator is None:
                    status = "missing"
                    message = (
                        f"primary={'present' if primary else 'missing'} "
                        f"validator={'present' if validator else 'missing'}"
                    )
                else:
                    differences = []
                    for field in ("open", "high", "low", "close"):
                        expected = float(getattr(primary, field))
                        actual = float(getattr(validator, field))
                        if not isclose(expected, actual, rel_tol=0.0, abs_tol=price_abs_tolerance):
                            differences.append(f"{field}={expected}/{actual}")
                    if not isclose(
                        primary.volume,
                        validator.volume,
                        rel_tol=volume_rel_tolerance,
                        abs_tol=100,
                    ):
                        differences.append(f"volume={primary.volume}/{validator.volume}")
                    status = "mismatch" if differences else "match"
                    message = "; ".join(differences) if differences else "OHLCV matched"
            except Exception as exc:  # Provider failures are validation evidence.
                status = "error"
                message = f"{type(exc).__name__}: {exc}"
            store.record_validation_result(
                primary_source, provider.code, symbol, trade_date, status, message, incident_id
            )
            counts[status] += 1
    return ValidationSummary(
        checked=sum(counts.values()),
        matched=counts["match"],
        mismatched=counts["mismatch"],
        missing=counts["missing"],
        errors=counts["error"],
    )
