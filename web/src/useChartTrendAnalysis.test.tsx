// @vitest-environment jsdom

import { act, renderHook, waitFor } from '@testing-library/react'
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
  vi.unstubAllGlobals()
})

describe('useChartTrendAnalysis', () => {
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
