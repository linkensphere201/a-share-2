// @vitest-environment jsdom

import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useChartTrendAnalysis } from './useChartTrendAnalysis'
import type { TrendAnalysisRun } from './trendAnalysisClient'

const latestRun: TrendAnalysisRun = {
  run_id: 'latest-run',
  as_of_date: '2026-09-05',
  completion_state: 'completed',
  stale: false,
  stale_reasons: [],
  warnings: [],
  items: [],
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('useChartTrendAnalysis', () => {
  it('hides previous-symbol evidence immediately and ignores a superseded parsed response', async () => {
    const pending: Array<(value: unknown) => void> = []
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve({
      ok: true, json: () => new Promise(resolve => pending.push(resolve)),
    })))
    const { result, rerender } = renderHook(({ symbol }) => useChartTrendAnalysis({ symbol, enabled: true }),
      { initialProps: { symbol: 'OLD.SZ' } })
    await waitFor(() => expect(pending).toHaveLength(1))
    await act(async () => pending[0]({ effective: latestRun }))
    expect(result.current.analysis).toEqual(latestRun)
    act(() => window.dispatchEvent(new CustomEvent('stock-harness:trend-analysis-updated', { detail: { symbol: 'OLD.SZ' } })))
    await waitFor(() => expect(pending).toHaveLength(2))
    rerender({ symbol: 'NEW.SZ' })
    expect(result.current.analysis).toBeNull()
    await waitFor(() => expect(pending).toHaveLength(3))
    await act(async () => pending[2]({ effective: { ...latestRun, run_id: 'new' } }))
    await act(async () => pending[1]({ effective: latestRun }))
    expect(result.current.analysis?.run_id).toBe('new')
  })

  it('orders same-symbol reloads and isolates disabled and historical sources', async () => {
    const pending: Array<(value: unknown) => void> = []
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve({
      ok: true, json: () => new Promise(resolve => pending.push(resolve)),
    })))
    const { result, rerender } = renderHook(({ enabled, override }: { enabled: boolean; override: TrendAnalysisRun | null }) =>
      useChartTrendAnalysis({ symbol: 'A', enabled, override }), { initialProps: { enabled: true, override: null as TrendAnalysisRun | null } })
    await waitFor(() => expect(pending).toHaveLength(1))
    act(() => window.dispatchEvent(new CustomEvent('stock-harness:trend-analysis-updated', { detail: { symbol: 'A' } })))
    await waitFor(() => expect(pending).toHaveLength(2))
    await act(async () => pending[1]({ effective: { ...latestRun, run_id: 'newest' } }))
    await act(async () => pending[0]({ effective: latestRun }))
    expect(result.current.analysis?.run_id).toBe('newest')
    rerender({ enabled: false, override: null })
    expect(result.current.analysis).toBeNull()
    rerender({ enabled: true, override: { ...latestRun, run_id: 'historical' } })
    expect(result.current.analysis?.run_id).toBe('historical')
    expect(result.current.preview).toBe(false)
  })

  it('treats a null override as following the latest result', async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        symbol: '000001.SZ',
        timeframe: 'daily',
        official: latestRun,
        preview: null,
        preview_expired: false,
        effective: latestRun,
      }),
    })
    vi.stubGlobal('fetch', fetch)

    const { result } = renderHook(() => useChartTrendAnalysis({
      symbol: '000001.SZ',
      enabled: true,
      override: null,
    }))

    await waitFor(() => expect(result.current.analysis?.run_id).toBe('latest-run'))
    expect(fetch).toHaveBeenCalledWith(
      '/api/analysis/trend/000001.SZ?timeframe=daily',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })

  it('uses a selected historical result without loading the latest result', () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    const historical = { ...latestRun, run_id: 'historical-run' }

    const { result } = renderHook(() => useChartTrendAnalysis({
      symbol: '000001.SZ',
      enabled: true,
      override: historical,
    }))

    act(() => undefined)
    expect(result.current.analysis?.run_id).toBe('historical-run')
    expect(fetch).not.toHaveBeenCalled()
  })
})
