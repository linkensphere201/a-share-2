# StockHarness

StockHarness is a performance-first stock visualization workstation. The existing `stock-picker` project is a reference for provider behavior and data semantics only.

Milestone 1 provides up to 30 years of full-scope daily OHLC and volume data, very fast incremental updates, responsive long-history charting, multi-symbol canvases, and persistent drawings. Instruments with shorter genuine histories begin at their first available trading date. Agent integration is deferred.

## Initial Scope

- Build an independent minimal data layer for instrument identity, trading dates, daily OHLC, volume, minimal provenance, and update timestamps.
- Treat data access, incremental refresh, chart zoom, and multi-canvas rendering performance as primary requirements.
- Use `stock-picker` only to verify provider calls, symbol conventions, and known data edge cases; do not copy its research architecture.
- Derive moving averages for charts and keep unrelated research, factor, strategy, backtest, report, snapshot, workflow, and worker code out of Milestone 1.

## Relationship To Workspace Docs

- Planning docs live in `../2026-07-27-stock-harness/`.
- The legacy implementation source is `../stock-picker/`.
- This repository is the implementation target for the upgraded StockHarness project.

## Status

The first data-layer slice is implemented with a minimal typed model, provider/storage protocols, a transactional SQLite performance baseline, and repeatable hot-path benchmarks. SQLite remains a baseline candidate until it is compared with an immutable yearly base plus a small incremental overlay at larger scale.

## Development

Run the dependency-free unit tests:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

Run the representative SQLite hot-path benchmark:

```powershell
$env:PYTHONPATH = "src"
python -m stock_harness.benchmarks.sqlite_hot_path `
  --output data/benchmarks/sqlite-hot-path.sqlite
```

## Provider Configuration

Committed examples live in `config/providers.example.yaml` and `config/storage.example.yaml`. Local `*.local.yaml` files are ignored. Provider credentials are loaded from the configured environment variable first, then from the configured ignored `.env` file; token values are never written to logs or configuration output.

Probe the configured Tushare account:

```powershell
$env:PYTHONPATH = "src"
..\stock-picker\.venv\Scripts\python.exe -m stock_harness.cli probe
```

Resume the full-market 30-year stock backfill:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_stock_backfill.ps1
```

The launcher processes at most 25 missing trading dates per Python process by default, then starts a fresh process to bound Provider and SQLite memory. Override this bounded batch with `-BatchDates`; use `-Once` only for a single diagnostic batch. Do not use an unbounded process for multi-decade collection. The backfill writes daily bars and their complete-day receipt in one transaction, and restarting the same command skips completed trading dates.

SQLite connection memory is configured in `config/storage.local.yaml`. The ingestion defaults are a 32 MiB page cache and 256 MiB mmap per connection; the mmap is required to retain historical insert throughput on the current store.

The canonical Tushare path uses a lightweight configured HTTP/JSON client and does not import the pandas-based Tushare SDK. SQLite writes use direct conditional UPSERT, cross-process writer serialization, and a PASSIVE checkpoint after each process batch.

Run the persistent Tushare gap-repair worker:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_gap_repair.ps1
```

An empty Tushare open date is queued without blocking the primary backfill. The repair worker uses Baostock to enumerate and repair the date's trading A-shares, then calls AkShare/Eastmoney only for unresolved symbols. Every repaired bar retains its actual Provider source; a later complete Tushare snapshot replaces fallback rows and completes the repair job.
