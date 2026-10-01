// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useChartDailyBars } from './useChartDailyBars'

vi.mock('./useIntradayDailyPolling', () => ({ useIntradayDailyPolling: vi.fn() }))
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('ignores parsed responses from a cancelled symbol request', async () => {
  let resolveOld!: (value: unknown) => void
  const oldBody = new Promise(resolve => { resolveOld = resolve })
  const current = { trade_date: '2026-09-30', open: 1, high: 2, low: 1, close: 2, volume: 10, source: 'test' }
  const fetcher = vi.fn()
    .mockResolvedValueOnce({ ok: true, json: () => oldBody })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ items: [current], instrument_kind: 'stock' }) })
  vi.stubGlobal('fetch', fetcher)
  const identity = vi.fn()
  const coverage = vi.fn()
  const { result, rerender } = renderHook(({ symbol }) => useChartDailyBars({
    symbol, onLoadStart: vi.fn(), onBeforePreserve: vi.fn(), onBarsChanged: vi.fn(),
    onCoverageChange: coverage, onInstrumentIdentity: identity,
  }), { initialProps: { symbol: 'OLD.SZ' } })
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1))
  rerender({ symbol: 'NEW.SZ' })
  await waitFor(() => expect(result.current.bars).toEqual([current]))
  await act(async () => resolveOld({ items: [], instrument_kind: 'sector' }))
  expect(result.current.bars).toEqual([current])
  expect(result.current.state).toBe('ready')
  expect(identity).toHaveBeenCalledTimes(1)
  expect(coverage).toHaveBeenCalledTimes(1)
})

it('ignores late parsing failures after switching symbols', async () => {
  let rejectOld!: (reason: Error) => void
  const oldBody = new Promise((_, reject) => { rejectOld = reject })
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce({ ok: true, json: () => oldBody })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ items: [] }) }))
  const { result, rerender } = renderHook(({ symbol }) => useChartDailyBars({
    symbol, onLoadStart: vi.fn(), onBeforePreserve: vi.fn(), onBarsChanged: vi.fn(),
    onInstrumentIdentity: vi.fn(),
  }), { initialProps: { symbol: 'OLD.SZ' } })
  await act(async () => {})
  rerender({ symbol: 'NEW.SZ' })
  await waitFor(() => expect(result.current.state).toBe('ready'))
  await act(async () => rejectOld(new Error('late parsing failure')))
  expect(result.current.state).toBe('ready')
})
