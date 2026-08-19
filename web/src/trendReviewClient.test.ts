import { describe, expect, it, vi } from 'vitest'
import { buildExpectedLabels, createTrendReview, updateTrendReview } from './trendReviewClient'
import { trendTradingSystemDefaults } from './tradingSystems'


describe('trend review client', () => {
  it('creates an isolated causal review request with the chart settings', async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ review: { review_id: 'review-1' }, analysis: { run_id: 'run-1' } }),
    })
    vi.stubGlobal('fetch', fetch)

    await createTrendReview({
      symbol: '000001.SZ', horizon: 'short',
      intervalStart: '2026-01-01', intervalEnd: '2026-07-31',
      asOfDate: '2026-08-18', classification: 'positive',
      tags: ['trend-line'], rationale: '', settings: trendTradingSystemDefaults,
    })

    const body = JSON.parse(fetch.mock.calls[0][1].body)
    expect(body).toMatchObject({
      symbol: '000001.SZ', timeframe: 'daily', horizon: 'short',
      as_of_date: '2026-08-18', short_horizon_bars: 60,
      medium_horizon_bars: 120, long_horizon_bars: 250,
    })
  })

  it('builds scoreable expected evidence from accepted and rejected labels', () => {
    const expected = buildExpectedLabels([
      { item_id: 'line-1', item_type: 'line', decision: 'accepted', payload: {}, rationale: '' },
      { item_id: 'pattern-1', item_type: 'pattern', decision: 'rejected', payload: {}, rationale: '' },
      { item_id: 'zone-1', item_type: 'zone', decision: 'ambiguous', payload: {}, rationale: '' },
    ])

    expect(expected.trend_lines).toHaveLength(1)
    expect(expected.forbidden).toHaveLength(1)
    expect(expected).not.toHaveProperty('key_levels')
  })

  it('uses optimistic revision when saving decisions', async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ revision: 4 }) })
    vi.stubGlobal('fetch', fetch)
    const review = {
      review_id: 'review-1', revision: 3, labels: [],
    } as unknown as Parameters<typeof updateTrendReview>[0]

    await updateTrendReview(review, 'proposed', [], 'reviewed')

    expect(JSON.parse(fetch.mock.calls[0][1].body)).toMatchObject({
      revision: 3, review_status: 'proposed', rationale: 'reviewed',
    })
  })
})
