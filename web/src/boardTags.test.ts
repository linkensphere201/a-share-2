// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { fetchInstrumentBoardMemberships } from './boardTags'

afterEach(() => vi.unstubAllGlobals())

it('batches full affiliation requests at 500 symbols and forwards cancellation', async () => {
  const signal = new AbortController().signal
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const symbols = new URL(String(input), 'http://localhost').searchParams.getAll('symbol')
    return new Response(JSON.stringify({ items: symbols.map(symbol => ({ symbol, boards: [] })) }))
  })
  vi.stubGlobal('fetch', fetchMock)
  const result = await fetchInstrumentBoardMemberships(Array.from({ length: 501 }, (_, i) => `${i}.SZ`), signal)
  expect(Object.keys(result)).toHaveLength(501)
  expect(fetchMock).toHaveBeenCalledTimes(2)
  expect(fetchMock.mock.calls[1][0]).toBe('/api/instrument-board-memberships?symbol=500.SZ')
  expect(fetchMock).toHaveBeenLastCalledWith(expect.any(String), { signal })
})

it('does not disguise failed membership requests as empty affiliation data', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 503 })))
  await expect(fetchInstrumentBoardMemberships(['000001.SZ'])).rejects.toThrow('HTTP 503')
  expect(await fetchInstrumentBoardMemberships([])).toEqual({})
})
