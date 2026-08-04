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

The first data-layer slice is implemented with a minimal typed model, provider/storage protocols, a transactional SQLite hot store, repeatable hot-path benchmarks, and real historical coverage for A-shares, ETFs, major indices, SW2021 sectors, and Provider-native Eastmoney/THS boards.

## Development

Create the local environment and run the complete test suite:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
powershell -ExecutionPolicy Bypass -File scripts\run_tests.ps1
```

The test runner keeps all pytest temporary data, pytest cache, Python bytecode, process temporary files, and npm cache under the fixed `.tmp\test` directory. Use `-BackendOnly` or `-FrontendOnly` for a scoped run.

Run the local chart-serving API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn --app-dir src stock_harness.api:app `
  --host 127.0.0.1 --port 8001
```

The API exposes instrument search and detail, bounded daily bars, board members, and reverse stock-to-board relationships under `/api`. Interactive OpenAPI documentation is available at `/docs`.

Start the web workstation in another terminal:

```powershell
Set-Location web
npm.cmd install
npm.cmd run dev
```

Open `http://127.0.0.1:5173`. The Vite server proxies `/api` to the local API on port 8001.

The workstation toolbar provides a window-group selector and a visible `布局管理` command on desktop. The graphical editor creates groups from templates, adds horizontal or vertical list/chart windows, configures list-to-chart relationships, selects the startup default, and supports undo/redo. Structural editing is intentionally hidden on mobile.

## Desktop Application

Build the Windows desktop package:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_desktop.ps1
```

The script always uses `build\desktop` for PyInstaller work files and replaces the single package at `dist\StockHarness\StockHarness.exe`; it does not create version-suffixed output directories. The application starts the local FastAPI service on stable loopback origin `http://127.0.0.1:8765`, serves the production React build from that origin, opens a WebView2 window, and stops the service when the window closes. WebView2 runs with a persistent profile under `%LOCALAPPDATA%\StockHarness\WebView`, so workspace layouts survive application restarts. WebView2 must be available on the machine.

The package contains credential-free `config\providers.local.yaml` and `config\storage.local.yaml` defaults. When the executable remains under this repository's `dist` directory, it detects and reuses the repository's ignored local configuration and existing database. A package copied elsewhere uses its own `config` directory and defaults to its own `data\market.sqlite`. Set `TUSHARE_TOKEN` in the environment or provide the ignored `.env` referenced by the active Provider configuration before relying on automatic updates.

At application startup, a background worker immediately refreshes the recent trading calendar and missing completed-day snapshots for stocks, exchange ETFs, configured indices, SW sectors, Eastmoney boards, and THS boards. It then polls every 15 minutes. The UI starts independently; Provider failures are retained as update status and retried on the next poll. The current trading day is eligible only after 18:00 local time, preventing an intraday partial daily snapshot from being marked complete.

Inspect the worker without blocking chart requests:

```text
GET /api/update-status
```

Use `--no-auto-update` for offline diagnostics and `--smoke-test` for a packaged backend/frontend startup check.

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

Resume the configured ETF, broad-index, and SW2021 level-one sector histories:

```powershell
$env:PYTHONPATH = "src"
..\stock-picker\.venv\Scripts\python.exe -m stock_harness.cli backfill-universe `
  --scope all --years 30
```

Refresh the complete searchable equity-ETF, Eastmoney-board, and THS-board catalogs:

```powershell
$env:PYTHONPATH = "src"
..\stock-picker\.venv\Scripts\python.exe -m stock_harness.cli sync-catalog --scope all
```

Resume their 30-year available histories and refresh current board memberships:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_expanded_backfill.ps1
```

The expanded catalog keeps acquisition and publisher provenance separate. Eastmoney rows use `acquired_via=tushare`, `source_system=eastmoney`, and daily source `tushare_dc`; THS rows use `source_system=ths` and daily source `tushare_ths`. `.DC` and `.TI` boards are never merged by display name. Explicit aliases and cross-Provider mappings have dedicated tables.

Write expanded catalog and history coverage:

```powershell
$env:PYTHONPATH = "src"
..\stock-picker\.venv\Scripts\python.exe -m stock_harness.cli expanded-report
```

Validate a Provider-native board against the matching AkShare publisher endpoint:

```powershell
$env:PYTHONPATH = "src"
..\stock-picker\.venv\Scripts\python.exe -m stock_harness.cli validate-board-date `
  --source ths --symbol 886033.TI --trade-date 2026-07-31
```

THS volume is normalized from lots to shares. Existing stores use the idempotent `ths-volume-shares` data migration; ETF-link funds are excluded from the searchable ETF catalog by the `exclude-etf-links` migration. Both migration IDs are recorded in SQLite and cannot be applied twice.

`config/providers.example.yaml` contains the explicit ETF and index whitelists and the inclusion reason for each symbol. ETF and index volumes are normalized from lots to shares; SW sector volumes are normalized from 10,000-share units to shares. Incomplete close-only pre-launch index rows are not fabricated into candles. Invalid SW sector rows are isolated and repaired from the independent AkShare/SW endpoint before their symbol cursor advances.

Write a per-kind and per-symbol coverage report:

```powershell
$env:PYTHONPATH = "src"
..\stock-picker\.venv\Scripts\python.exe -m stock_harness.cli coverage-report
```

The generated `data/reports/universe-coverage.json` records each symbol's first and latest stored trading date, row count, and Provider provenance. The `data/` directory remains local and ignored by Git.

The launcher processes at most 25 missing trading dates per Python process by default, then starts a fresh process to bound Provider and SQLite memory. Override this bounded batch with `-BatchDates`; use `-Once` only for a single diagnostic batch. Do not use an unbounded process for multi-decade collection. The backfill writes daily bars and their complete-day receipt in one transaction, and restarting the same command skips completed trading dates.

SQLite connection memory is configured in `config/storage.local.yaml`. The ingestion defaults are a 32 MiB page cache and 256 MiB mmap per connection; the mmap is required to retain historical insert throughput on the current store.

The canonical Tushare path uses a lightweight configured HTTP/JSON client and does not import the pandas-based Tushare SDK. SQLite writes use direct conditional UPSERT, cross-process writer serialization, and a PASSIVE checkpoint after each process batch.

Run the persistent Tushare gap-repair worker:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_gap_repair.ps1
```

An empty Tushare open date is queued without blocking the primary backfill. The repair worker uses Baostock to enumerate and repair the date's trading A-shares, then calls AkShare/Eastmoney only for unresolved symbols. Every repaired bar retains its actual Provider source; a later complete Tushare snapshot replaces fallback rows and completes the repair job.
