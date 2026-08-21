// @vitest-environment jsdom

import { renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { logWarning } from './eventLogger'
import { useIntradayDailyPolling } from './useIntradayDailyPolling'

vi.mock('./chartData', async importOriginal => ({
  ...(await importOriginal<typeof import('./chartData')>()),
  millisecondsUntilMarketSession: () => 0,
  millisecondsUntilNextMarketDay: () => 60_000,
}))

vi.mock('./eventLogger', () => ({
  logInfo: vi.fn(),
  logWarning: vi.fn(),
}))

describe('useIntradayDailyPolling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('logs the first polling failure again after the symbol changes', async () => {
    const onBar = vi.fn()
    const { rerender } = renderHook(
      ({ symbol }) => useIntradayDailyPolling({ symbol, onBar }),
      { initialProps: { symbol: '600519.SH' } },
    )
    await vi.advanceTimersByTimeAsync(0)
    expect(logWarning).toHaveBeenCalledTimes(1)

    rerender({ symbol: '000001.SZ' })
    await vi.advanceTimersByTimeAsync(0)

    expect(logWarning).toHaveBeenCalledTimes(2)
    expect(vi.mocked(logWarning).mock.calls[1][2]).toMatchObject({
      symbol: '000001.SZ',
    })
  })
})
