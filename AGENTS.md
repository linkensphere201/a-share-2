# StockHarness Agent Guide

## Purpose

This repository contains the implementation work for StockHarness, a performance-first stock visualization workstation.

## Boundaries

- Keep implementation code in this repository.
- Keep planning, decisions, and work logs in `../2026-07-27-stock-harness/`.
- Treat `../stock-picker/` as a read-only behavioral and data-contract reference. Do not copy its architecture or modules unless a later numbered task explicitly changes this decision.

## Working Rules

- Keep the chart-serving data model minimal and make data access and incremental-update latency first-order constraints.
- Prefer explicit configuration, typed data boundaries, and inspectable outputs.
- Record migration decisions in the project docs before making broad structural changes.
- Do not mix unrelated workspace documentation into this implementation repository.

## Routine Release Operations

The following repository-scoped workflows are routine and do not require a `CONFIRMED` dangerous-command prompt:

- Run `scripts/run_tests.ps1`; it keeps artifacts beneath `.tmp/test`.
- Run `scripts/build_desktop.ps1`; it reuses `build/desktop` and replaces only `dist/StockHarness`.
- Run `dist/StockHarness/StockHarness.exe --smoke-test` against the generated package.
- Stage task-owned files, create a normal commit, and push normally to this repository's configured remote branch.

This exception does not include arbitrary file deletion, force push, history rewriting, destructive Git operations, or stopping unrelated processes.
