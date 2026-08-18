import { afterEach, describe, expect, it, vi } from 'vitest'
import { recalculateTrendAnalysis } from './trendAnalysisClient'
import { trendTradingSystemDefaults } from './tradingSystems'

describe('trend analysis client', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('submits only explicitly selected timeframes and configured horizons', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ status: 'completed' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    ))
    vi.stubGlobal('fetch', fetchMock)

    await recalculateTrendAnalysis('000001.SZ', {
      ...trendTradingSystemDefaults,
      weeklyEnabled: true,
      monthlyEnabled: false,
    }, 4)

    const init = fetchMock.mock.calls[0][1] as RequestInit
    const payload = JSON.parse(String(init.body))
    expect(payload.symbol).toBe('000001.SZ')
    expect(payload.timeframes).toEqual(['daily', 'weekly'])
    expect(payload.long_horizon_bars).toBe(250)
    expect(payload.config_version).toContain('workspace-r4')
  })

  it('surfaces backend failure details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: 'no analysis bars' }),
      { status: 422, headers: { 'Content-Type': 'application/json' } },
    )))

    await expect(recalculateTrendAnalysis(
      '000001.SZ', trendTradingSystemDefaults, 0,
    )).rejects.toThrow('no analysis bars')
  })
})
