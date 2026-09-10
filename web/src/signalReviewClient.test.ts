import { afterEach, describe, expect, it, vi } from 'vitest'

import { listSignalScores } from './signalReviewClient'


describe('signalReviewClient', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('loads every score page when multiple independent systems exceed the API page size', async () => {
    const first = Array.from({ length: 5000 }, (_, index) => ({
      symbol: `B${index}`, system_id: 'board-hotspot-emergence',
    }))
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = String(input)
      const payload = url.includes('offset=5000')
        ? { items: [{ symbol: 'LAST', system_id: 'trend-breakout' }] }
        : { items: first, total: 5001 }
      return new Response(JSON.stringify(payload), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const result = await listSignalScores('run-1')

    expect(result).toHaveLength(5001)
    expect(result.at(-1)?.symbol).toBe('LAST')
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
