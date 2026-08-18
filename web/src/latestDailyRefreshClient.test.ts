import { afterEach, expect, it, vi } from 'vitest'
import { refreshLatestDailyBar } from './latestDailyRefreshClient'

afterEach(() => vi.unstubAllGlobals())

function response(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response
}

it('returns an isolated provisional bar during the market session', async () => {
  const fetchMock = vi.fn().mockResolvedValue(response({
    items: [{ trade_date: '2026-08-18', close: 12, bar_state: 'intraday' }],
    status: { state: 'ready' },
  }))
  vi.stubGlobal('fetch', fetchMock)

  const result = await refreshLatestDailyBar(
    '000001.SZ', new Date('2026-08-18T14:30:00+08:00'),
  )

  expect(result.mode).toBe('provisional')
  expect(result.warning).toBe(false)
  expect(result.items).toHaveLength(1)
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

it('uses canonical history when the main store already covers today', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(response({
      items: [], canonical_symbols: ['000001.SZ'], status: { state: 'ready' },
    }))
    .mockResolvedValueOnce(response({ items: [{ trade_date: '2026-08-18', close: 12 }] }))
  vi.stubGlobal('fetch', fetchMock)

  const result = await refreshLatestDailyBar(
    '000001.SZ', new Date('2026-08-18T14:30:00+08:00'),
  )

  expect(result.mode).toBe('canonical')
  expect(result.items).toHaveLength(1)
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

it('waits for a post-close update and returns a warning without hiding bars', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(response({ accepted: true, state: 'running' }, 202))
    .mockResolvedValueOnce(response({ state: 'warning', rows_changed: 2, error: 'sector warning' }))
    .mockResolvedValueOnce(response({ items: [{ trade_date: '2026-08-18', close: 12 }] }))
  vi.stubGlobal('fetch', fetchMock)

  const result = await refreshLatestDailyBar(
    '000001.SZ', new Date('2026-08-18T18:30:00+08:00'),
  )

  expect(result).toMatchObject({
    mode: 'final', warning: true, rowsChanged: 2, error: 'sector warning',
  })
  expect(result.items).toHaveLength(1)
})
