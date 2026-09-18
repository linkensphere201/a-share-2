import { afterEach, expect, it, vi } from 'vitest'
import { listScreenerCandidates, loadScreenerCandidate } from './screenerClient'

afterEach(() => vi.unstubAllGlobals())

it('reads successive bounded summary pages and keeps detail requests separate', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [{ rank: 1 }], has_more: true })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [{ rank: 2 }], has_more: false })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ rank: 2, evidence: { kind: 'bull-flag-range' } })))
  vi.stubGlobal('fetch', fetchMock)
  const signal = new AbortController().signal
  expect(await listScreenerCandidates('run/id', signal)).toEqual([{ rank: 1 }, { rank: 2 }])
  expect(fetchMock).toHaveBeenNthCalledWith(1,
    '/api/screener/runs/run%2Fid/candidates?limit=200&offset=0&summary=true', { signal })
  expect(fetchMock).toHaveBeenNthCalledWith(2,
    '/api/screener/runs/run%2Fid/candidates?limit=200&offset=1&summary=true', { signal })
  expect((await loadScreenerCandidate('run/id', 2, signal)).rank).toBe(2)
  expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/screener/runs/run%2Fid/candidates/2', { signal })
})
