# Futures Acceptance Checklist

Run the progressive acceptance report from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_futures_acceptance.ps1

powershell -ExecutionPolicy Bypass -File scripts\run_futures_acceptance.ps1 `
  -RunTests -BuildFrontend `
  -BaseUrl http://127.0.0.1:8765 `
  -RealSymbol FUT:SHFE:CU:202609 `
  -ContinuousSymbol FUTCONT:SHFE:CU:MAIN:raw
```

The fixed report is `.tmp/test/futures-acceptance-report.json`. Store integrity evidence is written to ignored `data/reports/futures-integrity-acceptance.json`. Existing files are overwritten in place; the script does not create versioned build/test directories, package the APP, mutate market data, or delete files.

## Automated Gate

- Backend and frontend suites pass through the fixed test runner.
- The production frontend compiles.
- Futures integrity audit completes with reviewable structural, coverage, mapping, roll, and receipt evidence.
- A running APP reports healthy status and non-empty futures discovery.
- The selected real and continuous instruments have daily history.
- The selected continuous series exposes mapping and rollover/build evidence.

## Manual Gate

The generated report always retains manual items as `manual-pending`; edit or transcribe acceptance evidence after testing. Required items cover:

1. Day-session provisional refresh, failure fallback, and retained stale data.
2. Night-session trading-day ownership and later canonical takeover.
3. Real/continuous identity, settlement, contract volume, MACD, and open-interest panes.
4. Thirty-year normal/log zoom, free-space pan, extrema, gaps, and rollover measurement warning.
5. Drawing creation, endpoint/line movement, persistence, and identity isolation.
6. Attached/detached lists, groups, multi-window layout, and restart persistence.
7. Trend preview/official revisions, causal evidence, and rollover qualification.
8. Bottom status events, rotating logs, and secret-free failure evidence.
9. Read-only MCP futures tools and the `stock-harness-analysis` Skill.
10. Packaged restart and offline retained-history degradation.
11. Four-chart and six-chart target-machine responsiveness.

`m6_complete` is deliberately always `false` in generated reports. Only `M6.12.5` may set the final disposition after all manual evidence, Provider limitations, archive coverage, package identity, and performance measurements are recorded.
