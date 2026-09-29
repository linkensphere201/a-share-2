import { afterEach, expect, it, vi } from 'vitest'
import { listScreenerCandidates, loadScreenerCandidate, listScreenerRuns } from './screenerClient'

afterEach(() => vi.unstubAllGlobals())

it('reads history beyond ten runs using bounded pages', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: Array.from({ length: 100 }, (_, i) => ({ run_id: `run-${i}` })) })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [{ run_id: 'old-run' }] })))
  vi.stubGlobal('fetch', fetchMock)
  const result = await listScreenerRuns(undefined, 150)
  expect(result).toHaveLength(101)
  expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/screener/runs?limit=100&offset=0', { signal: undefined })
  expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/screener/runs?limit=50&offset=100', { signal: undefined })
})

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
