// @vitest-environment jsdom

import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ActiveMarketValueReadout } from './ActiveMarketValueReadout'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('shows compact final active-market-value diagnostics', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true,
    json: async () => ({
      symbol: 'SHAMV.A', status: 'ready', algorithm_version: 'sh-amv-cyf-v1',
      smoothing_period: 13, latest_absolute_close: 12_345_000_000_000,
      last_trade_date: '2026-09-04', latest_coverage_ratio: 0.998,
      latest_change_percent: -1.25,
    }),
  } as Response)))

  render(<ActiveMarketValueReadout symbol="SHAMV.A"/>)

  expect(await screen.findByText('活跃 12.35万亿')).toBeTruthy()
  expect(screen.getByText('截至 09-04')).toBeTruthy()
  expect(screen.getByText('-1.25%').classList.contains('fall')).toBe(true)
  expect(screen.getByText('覆盖 99.8%')).toBeTruthy()
  expect(screen.getByText('CYF13')).toBeTruthy()
})

it('does not query or render for ordinary instruments', () => {
  const fetcher = vi.fn()
  vi.stubGlobal('fetch', fetcher)
  render(<ActiveMarketValueReadout symbol="600519.SH"/>)
  expect(fetcher).not.toHaveBeenCalled()
  expect(document.querySelector('.amv-title-readout')).toBeNull()
})

it('opens materialized contribution and broad-index diagnostics', async () => {
  vi.stubGlobal('fetch', vi.fn(async input => {
    const url = String(input)
    return {
      ok: true,
      json: async () => url.includes('/diagnostics/latest') ? ({
        status: 'ready', trade_date: '2026-09-04', eligible_count: 5500,
        total_count: 5556, input_digest: 'abcdef0123456789',
        contributors: [
          { direction: 'positive', rank: 1, symbol: '000001.SZ', name: '平安银行', active_close: 1, change_contribution: 200_000_000 },
          { direction: 'negative', rank: 1, symbol: '600000.SH', name: '浦发银行', active_close: 1, change_contribution: -100_000_000 },
        ],
        comparators: [
          { symbol: '000300.SH', name: '沪深300', change_percent: -0.2, divergence_percent_points: 0.35 },
        ],
      }) : ({
        symbol: 'SHAMV.A', status: 'ready', algorithm_version: 'sh-amv-cyf-v1',
        smoothing_period: 13, latest_absolute_close: 12_345_000_000_000,
        last_trade_date: '2026-09-04', latest_coverage_ratio: 0.99,
        latest_change_percent: 0.15,
      }),
    } as Response
  }))

  render(<ActiveMarketValueReadout symbol="SHAMV.A"/>)
  fireEvent.click(await screen.findByRole('button', { name: '打开活跃市值诊断' }))

  expect(await screen.findByRole('dialog', { name: '活跃市值诊断' })).toBeTruthy()
  expect(await screen.findByText('5500 / 5556')).toBeTruthy()
  expect(screen.getByText('平安银行')).toBeTruthy()
  expect(screen.getByText('浦发银行')).toBeTruthy()
  expect(screen.getByText('+0.35 pct')).toBeTruthy()
})
