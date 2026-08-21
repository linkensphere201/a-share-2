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

it('lets the backend apply futures sessions and reloads the fused provisional bar', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(response({
      items: [], status: { state: 'disabled' },
      futures: { mode: 'provisional', state: 'ready', received_count: 1 },
    }))
    .mockResolvedValueOnce(response({ items: [
      { trade_date: '2026-08-20', close: 100, bar_state: 'final' },
      { trade_date: '2026-08-21', close: 102, bar_state: 'intraday' },
    ] }))
  vi.stubGlobal('fetch', fetchMock)

  const result = await refreshLatestDailyBar(
    'FUT:SHFE:CU:202609', new Date('2026-08-21T21:30:00+08:00'),
  )

  expect(fetchMock.mock.calls[0][0]).toBe('/api/intraday/refresh')
  expect(result).toMatchObject({
    mode: 'provisional', feedback: 'success', warning: false,
  })
  expect(result.items).toEqual([
    expect.objectContaining({ trade_date: '2026-08-21', bar_state: 'intraday' }),
  ])
})

it('keeps the last futures bar and reports a provider fallback', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(response({
      items: [], status: { state: 'disabled' },
      futures: {
        mode: 'provisional', state: 'error', last_error: 'spot unavailable',
      },
    }))
    .mockResolvedValueOnce(response({ items: [
      { trade_date: '2026-08-21', close: 101, bar_state: 'intraday', stale: true },
    ] }))
  vi.stubGlobal('fetch', fetchMock)

  const result = await refreshLatestDailyBar(
    'FUTCONT:SHFE:CU:MAIN:raw', new Date('2026-08-21T10:30:00+08:00'),
  )

  expect(result).toMatchObject({
    mode: 'provisional', feedback: 'fallback', warning: true,
    error: 'spot unavailable',
  })
  expect(result.items).toHaveLength(1)
})

it('reports a closed futures session without starting the stock final-update path', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(response({
      items: [], status: { state: 'disabled' },
      futures: {
        mode: 'provisional', state: 'skipped', skip_reason: 'market-closed',
      },
    }))
    .mockResolvedValueOnce(response({ items: [
      { trade_date: '2026-08-20', close: 100, bar_state: 'final' },
    ] }))
  vi.stubGlobal('fetch', fetchMock)

  const result = await refreshLatestDailyBar(
    'FUT:SHFE:CU:202609', new Date('2026-08-21T16:00:00+08:00'),
  )

  expect(fetchMock.mock.calls[0][0]).toBe('/api/intraday/refresh')
  expect(result).toMatchObject({ feedback: 'skipped', warning: false, items: [] })
})

it('distinguishes a missing futures calendar from an unresolved contract', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(response({
      items: [], status: { state: 'disabled' },
      futures: {
        mode: 'provisional', state: 'skipped', skip_reason: 'calendar-unavailable',
      },
    }))
    .mockResolvedValueOnce(response({ items: [] }))
  vi.stubGlobal('fetch', fetchMock)

  const result = await refreshLatestDailyBar(
    'FUT:SHFE:CU:202609', new Date('2026-08-21T13:30:00+08:00'),
  )

  expect(result).toMatchObject({
    feedback: 'skipped', warning: true,
    message: '\u671f\u8d27\u4ea4\u6613\u65e5\u5386\u5c1a\u672a\u5c31\u7eea\uff0c\u5df2\u4fdd\u7559\u73b0\u6709\u6570\u636e',
  })
})

it('reports canonical takeover when a successful futures refresh is suppressed by final data', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(response({
      items: [], status: { state: 'disabled' },
      futures: { mode: 'provisional', state: 'ready', received_count: 1 },
    }))
    .mockResolvedValueOnce(response({ items: [
      { trade_date: '2026-08-21', close: 103, bar_state: 'final' },
    ] }))
  vi.stubGlobal('fetch', fetchMock)

  const result = await refreshLatestDailyBar(
    'FUTCONT:SHFE:CU:MAIN:raw', new Date('2026-08-21T14:00:00+08:00'),
  )

  expect(result).toMatchObject({
    mode: 'canonical', feedback: 'canonical', warning: false,
  })
  expect(result.items).toHaveLength(1)
})
