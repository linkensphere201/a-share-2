// @vitest-environment jsdom

import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
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
