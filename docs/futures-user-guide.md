# Futures User Guide

StockHarness treats futures as daily-frequency market instruments. Existing stock workflows remain unchanged; futures add real delivery-month contracts and derived continuous series with explicit settlement, open-interest, session, mapping, and rollover evidence.

## Find And Open Futures

Enable futures in local Provider configuration and restart the APP. In the instrument editor, select the futures classification and narrow by exchange, product, lifecycle, series kind, or price basis. A real contract uses an identity such as `FUT:SHFE:CU:202609`. A continuous series uses an identity such as `FUTCONT:SHFE:CU:MAIN:raw`.

Real contracts are suitable for inspecting the exact traded instrument and its expiry lifecycle. Continuous series are suitable for long-history trend context. They are derived from dated Tushare mappings and are not exchange-traded instruments.

Lists, custom groups, attached/detached chart windows, multi-window layouts, and persisted per-window state accept futures through the same graphical workflows as stocks. Active-group provisional polling is bounded to the futures references resolved from the currently open group.

## Read The Daily Chart

The shared chart supports normal/log price coordinates, 1M through ALL ranges, zoom, free-space pan with edge snap, high/low labels, gap shading, volume, MACD, and persisted visible range. Futures readouts add:

- contract or continuous identity and current mapped real contract;
- previous-settlement change semantics when available;
- settlement and previous settlement;
- contract volume and CNY amount when supplied;
- open interest and open-interest change;
- final versus forming/stale state;
- rollover markers for materialized continuous rows.

Volume is measured in contracts, not shares. Open interest is a position stock, not daily turnover. A continuous-series rollover can create a price jump and contract-volume discontinuity that is not an ordinary market gap. Range measurements crossing a rollover are marked non-comparable.

The open-interest pane is futures-only and optional. Volume and MACD retain their independent hide controls and pane ratios. Narrow chart windows progressively hide secondary readout fields before hiding the complete header.

## Raw And Adjusted Continuous Series

`raw` continuous history substitutes the mapped real contract without changing prices. It preserves genuine contract prices but may jump at rollover. Backward-ratio series adjust historical prices from same-day outgoing/incoming overlap so long trends are visually continuous; adjustment fails closed when required overlap evidence is missing.

Every materialized row retains its mapped contract. Build status retains source, date range, basis, rule version, digest, warnings, and roll events. Raw corrections rebuild only the dirty suffix with prior-day context. Adjusted corrections rebuild the full series because a later rollover factor can affect all earlier prices.

## Drawings And Window State

Drawings belong to the exact chart identity. A line drawn on a real contract does not silently appear on a continuous series. Continuous drawings also retain price basis and rule version so a rebuild or basis change cannot reinterpret old anchors without evidence.

Trend lines, colors, line styles, visibility, endpoints, horizontal/vertical conversion, toolbar state, panes, coordinate mode, and visible range use the existing persistent chart-window state. Switching an attached chart to another instrument restores that instrument's drawings while preserving window-owned range mode and controls according to the shared workspace contract.

## Refresh And Session State

The refresh button is server-owned:

- During a configured open day or night session, it requests only the selected real contract or the contract resolved from a continuous mapping.
- Outside a session, it skips provisional polling. After the configured final-data cutoff it can queue the canonical completed-day increment.
- A successful forming bar stays in isolated provisional storage and can survive restart.
- A failed refresh retains the prior forming bar, marks it stale, and emits a bounded warning.
- A same-date Tushare final bar always wins; the forming row remains only for takeover audit.

Night-session timestamps retain both calendar date and resolved futures trading day. Products without an explicit night rule do not poll at night. The service does not bridge a long exchange holiday by guessing a future trading day.

## Trend Trading System

Manual trend analysis can run on exact real contracts and on materialized continuous series. Futures evidence records instrument type, units, settlement basis, mapping/basis/rule details, and rollover count.

Raw continuous analysis excludes history before the latest rollover segment when ordinary price geometry would be misleading and excludes rollover volume from price-volume interpretation. Adjusted series may retain longer price history, but rollover qualification remains visible. Forming bars create preview-only analysis; later canonical takeover creates a separate official revision. Causal replay never reads future bars.

## Codex And MCP

The local read-only MCP server exposes bounded futures discovery, coverage, daily bars, continuous mappings/build/roll provenance, active workspace references, and persisted trend evidence. Use the `stock-harness-analysis` Skill for futures analysis so responses preserve:

- exact real versus continuous identity;
- price basis, rule version, and mapped-contract evidence;
- settlement, volume-contract, amount, and open-interest units;
- final versus forming/stale source dates;
- rollover qualification and analytical uncertainty.

MCP cannot access accounts, positions, orders, credentials, arbitrary Provider calls, unrestricted SQL, or mutation APIs.

## Known Limitations

- M6 is daily-only. It does not collect or display 15-minute or tick history.
- Forming daily bars are best-effort AKShare observations for active-window contracts, not an exchange-grade real-time feed.
- Night sessions must be configured per product from current exchange rules; missing rules fail closed.
- Historical Tushare rows without representable OHLC are rejected and retained as partial-receipt counts, not repaired with fabricated prices.
- Official/public validation endpoints can be unavailable or disagree on volume/open interest; comparison evidence never overwrites Tushare canonical storage.
- ETF/stock market-cap fields and stock-specific board semantics do not apply to futures.
- A continuous series is a derived analytical view, not a tradable contract and not a profit/backtest substitute.
- Backward-additive identity is reserved by the schema but its materializer is not implemented; current adjusted builds support backward-ratio only and fail closed for additive requests.
- The fixed Windows package passes offline backend/frontend smoke startup. Final four/six-chart interaction performance, packaged visual inspection, full archive size, and real day/night takeover evidence remain M6 acceptance items until recorded in the project report.

For configuration, history, backup, recovery, and troubleshooting, see [`futures-operations.md`](futures-operations.md).
