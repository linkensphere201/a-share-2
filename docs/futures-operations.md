# Futures Operations

StockHarness futures support is daily-frequency and opt-in. Tushare is the canonical source for contract metadata, calendars, mappings, and completed daily bars. AKShare supplies only the currently forming daily bar for contracts referenced by the active window group. It never repairs or overwrites canonical history.

## Configuration

Copy the futures section from `config/providers.example.yaml` into the ignored `config/providers.local.yaml`, review the exchange and product session rules, then set `providers.futures.enabled: true`. Keep the token in `TUSHARE_TOKEN` or the ignored `.env`; never put it in YAML.

The committed example enables all six domestic exchanges but leaves the parent futures switch off until M6 acceptance completes. Day sessions have conservative defaults. Night sessions are product-specific and fail closed when absent, so review current exchange rules before adding a product.

Probe permissions without writing market data:

```powershell
.\.venv\Scripts\python.exe -m stock_harness.cli probe-futures `
  --exchange CFFEX --exchange DCE --exchange CZCE `
  --exchange SHFE --exchange INE --exchange GFEX `
  --as-of 2026-08-21
```

## Initial History

The canonical backfill is resumable and lifecycle-bounded:

```powershell
.\.venv\Scripts\python.exe -m stock_harness.cli backfill-futures `
  --exchange CFFEX --exchange DCE --exchange CZCE `
  --exchange SHFE --exchange INE --exchange GFEX `
  --start-date 1990-01-01 --end-date 2026-08-21
```

Each successful contract window writes bars before its cursor. Rerunning skips completed windows and repairs failed windows. Unrepresentable Tushare rows are not fabricated: valid rows persist, the full request window advances, and a `partial` receipt retains `rejected_rows=N` for audit.

After canonical history and mappings converge, materialize continuous series:

```powershell
.\.venv\Scripts\python.exe -m stock_harness.cli build-futures-continuous `
  --start-date 1990-01-01 --end-date 2026-08-21

.\.venv\Scripts\python.exe -m stock_harness.cli audit-futures-store `
  --output data\reports\futures-integrity.json
```

Do not run two canonical backfills or a continuous build and canonical backfill concurrently. One background writer plus normal reads is the supported initial-load topology.

## Startup Updates

When futures are enabled, APP startup runs the same final-data updater as stocks. It refreshes catalog and calendars, checks the configured final-data cutoff, reads only missing tails plus the configured correction window, and updates affected mappings. Final bars are requested once per exchange and open trading day, then distributed to the applicable contract cursors. Main-contract mappings are requested once per trading day and reused across exchange passes. A failed daily batch does not advance affected cursors; a failed mapping batch falls back to the bounded per-series path. A no-change run does not rewrite history.

During an open configured session, only real contracts referenced by the active window group are polled. Continuous references resolve through the current canonical mapping, references are deduplicated, and `max_contracts` bounds the request. Polling stops outside configured day/night sessions. A failed refresh retains the prior provisional row and marks it stale. A same-date Tushare final row takes precedence and retains the provisional observation only as takeover audit evidence.

## Storage Budget

Futures storage shares `data/market.sqlite` with stocks. The final budget depends on genuine contract lifecycles and available mappings. Before the final archive measurement, reserve approximately 1-2 GiB of additional space for roughly 2-4 million real plus materialized continuous daily rows, indexes, receipts, mappings, roll evidence, and WAL headroom. This is a planning range, not a measured final size; `M6.12.5` must replace it with actual row and file-size evidence.

The configured 32 MiB SQLite page cache and 256 MiB mmap are process address-space settings, not additional database-file allocation. Do not create multiple read pools: the measured six-reader experiment regressed latency materially.

## Backup And Recovery

For a consistent manual backup, close StockHarness, verify no backfill process is running, and copy `market.sqlite` to a dated backup location. If `market.sqlite-wal` or `market.sqlite-shm` still exists because another process is open, stop and locate that process instead of copying an inconsistent subset.

Schema migration is automatic and idempotent. A failed or newer-than-supported futures schema disables futures storage while preserving existing stock tables. Keep the failed database intact, collect `%LOCALAPPDATA%\StockHarness\logs\stock-harness.log`, and restore from a verified copy only after preserving the current file under a different name. Recovery must never delete the only database copy.

Backfill recovery is a normal rerun of the same command and date range. Continuous-build recovery is also a rerun: completed clean series skip, dirty raw series rebuild a bounded suffix, and adjusted series rebuild fully.

## Troubleshooting

| Symptom | Evidence | Action |
|---|---|---|
| No futures in search | `/api/futures/coverage`, futures config | Enable parent futures switch, restart, then verify catalog discovery |
| Permission or source failure | `probe-futures`, rotating log error type | Verify `TUSHARE_TOKEN`, account permissions, network, and configured rate limit |
| `partial` daily receipts | integrity report `receipt_status_counts` | Inspect rejected-row counts; do not manufacture OHLC or repeatedly clear cursors |
| Provisional bar absent | refresh status and `skip_reason` | Confirm active-window reference, exchange calendar, product session, and contract mapping |
| Provisional bar stale | bottom WARN/ERROR bar, takeover audit | Keep retained data visible, check AKShare availability, and wait for canonical final takeover |
| Continuous series missing | mapping and build sections of integrity report | Finish canonical mappings, rerun continuous build, then audit dirty/unmapped series |
| Slow suffix rebuild | `futures_continuous_build_*` duration logs | Confirm current code uses bounded predecessor context and that no competing backfill runs |
| Futures unavailable after migration | `futures_storage_status`, migration error type | Keep stock mode operational, preserve DB/logs, and use a compatible application build |

Generated validation and integrity reports stay under ignored `data/reports`. They are local operational evidence and must not be committed with credentials or market data.
