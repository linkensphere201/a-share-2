import { expect, it, vi } from 'vitest'
import { trendTradingSystemDefaults } from './tradingSystems'
import { refreshThenRecalculateTrend } from './trendRefreshCoordinator'


it('publishes refreshed bars before starting trend recalculation', async () => {
  const order: string[] = []
  const refresh = vi.fn(async () => {
    order.push('refresh')
    return {
      mode: 'provisional' as const, items: [], warning: false, status: 'ready',
      feedback: 'success' as const, message: 'refreshed',
    }
  })
  const onRefresh = vi.fn(() => order.push('publish'))
  const recalculate = vi.fn(async () => {
    order.push('analyze')
    return { status: 'completed' }
  })

  const outcome = await refreshThenRecalculateTrend(
    '000001.SZ', trendTradingSystemDefaults, 2, { onRefresh },
    { refresh, recalculate },
  )

  expect(order).toEqual(['refresh', 'publish', 'analyze'])
  expect(outcome.refresh?.mode).toBe('provisional')
  expect(outcome.refreshError).toBeUndefined()
})


it('retains refresh failure evidence and still analyzes the last usable bars', async () => {
  const refreshError = new Error('quote provider unavailable')
  const onRefreshError = vi.fn()
  const recalculate = vi.fn().mockResolvedValue({ status: 'completed' })

  const outcome = await refreshThenRecalculateTrend(
    '000001.SZ', trendTradingSystemDefaults, 0, { onRefreshError },
    { refresh: vi.fn().mockRejectedValue(refreshError), recalculate },
  )

  expect(onRefreshError).toHaveBeenCalledWith(refreshError)
  expect(recalculate).toHaveBeenCalledOnce()
  expect(outcome.refreshError).toBe(refreshError)
  expect(outcome.analysis).toEqual({ status: 'completed' })
})


it('does not hide an analysis failure behind a successful refresh', async () => {
  const analysisError = new Error('analysis failed')

  await expect(refreshThenRecalculateTrend(
    '000001.SZ', trendTradingSystemDefaults, 0, {},
    {
      refresh: vi.fn().mockResolvedValue({
        mode: 'canonical', items: [], warning: false, status: 'ready',
        feedback: 'canonical', message: 'canonical',
      }),
      recalculate: vi.fn().mockRejectedValue(analysisError),
    },
  )).rejects.toBe(analysisError)
})


it('does not report a publication callback failure as a provider refresh failure', async () => {
  const publicationError = new Error('bar publication failed')
  const onRefreshError = vi.fn()
  const recalculate = vi.fn()

  await expect(refreshThenRecalculateTrend(
    '000001.SZ', trendTradingSystemDefaults, 0,
    {
      onRefresh: () => { throw publicationError },
      onRefreshError,
    },
    {
      refresh: vi.fn().mockResolvedValue({
        mode: 'canonical', items: [], warning: false, status: 'ready',
        feedback: 'canonical', message: 'canonical',
      }),
      recalculate,
    },
  )).rejects.toBe(publicationError)

  expect(onRefreshError).not.toHaveBeenCalled()
  expect(recalculate).not.toHaveBeenCalled()
})
