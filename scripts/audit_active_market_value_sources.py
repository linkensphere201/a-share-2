"""Audit Tushare fields required by the active-market-value milestone."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

from stock_harness.config import load_runtime_settings
from stock_harness.tushare_provider import TushareDailyProvider, _field, _iter_rows


FIELDS = (
    "ts_code", "trade_date", "turnover_rate_f", "free_share", "circ_mv",
    "total_mv", "close", "turnover_rate",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-config", type=Path, default=Path("config/providers.local.yaml"))
    parser.add_argument("--storage-config", type=Path, default=Path("config/storage.local.yaml"))
    parser.add_argument("--date", action="append", type=date.fromisoformat, dest="dates")
    args = parser.parse_args()
    dates = args.dates or [
        date(2026, 9, 4), date(2020, 1, 2), date(2010, 1, 4), date(1996, 1, 2),
    ]
    settings = load_runtime_settings(args.provider_config, args.storage_config)
    provider = TushareDailyProvider(settings.tushare)
    results = []
    for trade_date in dates:
        try:
            rows = list(_iter_rows(provider._call(
                "daily_basic", trade_date=trade_date.strftime("%Y%m%d"),
                fields=",".join(FIELDS),
            )))
            non_null = {
                field: sum(_field(row, field) not in (None, "") for row in rows)
                for field in FIELDS
            }
            results.append({
                "trade_date": trade_date.isoformat(), "status": "ok",
                "row_count": len(rows), "non_null": non_null,
            })
        except Exception as exc:
            results.append({
                "trade_date": trade_date.isoformat(), "status": "error",
                "error_type": type(exc).__name__, "message": str(exc)[:300],
            })
    print(json.dumps({"provider": "tushare", "dataset": "daily_basic", "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
