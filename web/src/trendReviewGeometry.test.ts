import { describe, expect, it } from 'vitest'
import {
  mergeReviewLabelsIntoAnalysis,
  reviewGeometryHandles,
  updateReviewGeometryHandle,
} from './trendReviewGeometry'
import type { TrendReviewLabel } from './trendReviewClient'

describe('trend review geometry', () => {
  it('extracts and updates line endpoints without mutating the source label', () => {
    const label: TrendReviewLabel = {
      item_id: 'line-1', item_type: 'line', decision: 'accepted', rationale: '',
      payload: {
        kind: 'support', first_pivot_date: '2026-01-02', first_price: 10,
        second_pivot_date: '2026-02-02', second_price: 12,
      },
    }

    expect(reviewGeometryHandles(label).map(item => item.id)).toEqual(['line:first', 'line:second'])
    const updated = updateReviewGeometryHandle(label, 'line:second', { date: '2026-02-03', price: 12.5 })

    expect(updated.payload).toMatchObject({ second_pivot_date: '2026-02-03', second_price: 12.5 })
    expect(label.payload).toMatchObject({ second_pivot_date: '2026-02-02', second_price: 12 })
  })

  it('updates pattern pivots, interval, neckline, and nested boundary endpoints', () => {
    const label: TrendReviewLabel = {
      item_id: 'pattern-1', item_type: 'pattern', decision: 'pending', rationale: '',
      payload: {
        display_name: '双底', neckline_price: 12,
        pivots: [
          { pivot_date: '2026-01-10', price: 10 },
          { pivot_date: '2026-02-10', price: 12 },
          { pivot_date: '2026-03-10', price: 10.2 },
        ],
        boundary_geometry: { upper: {
          start_date: '2026-01-10', start_price: 12,
          end_date: '2026-03-10', end_price: 12.2,
        } },
      },
    }

    const pivot = updateReviewGeometryHandle(label, 'pattern:pivot:0', { date: '2026-01-08', price: 9.8 })
    const boundary = updateReviewGeometryHandle(pivot, 'pattern:boundary:upper:end', { date: '2026-03-12', price: 12.4 })
    const neckline = updateReviewGeometryHandle(boundary, 'pattern:neckline', { price: 12.1 })

    expect(neckline.payload).toMatchObject({ start_date: '2026-01-08', end_date: '2026-03-10', neckline_price: 12.1 })
    expect((neckline.payload.pivots as Array<Record<string, unknown>>)[0]).toEqual({ pivot_date: '2026-01-08', price: 9.8 })
    expect(neckline.payload.boundary_geometry).toMatchObject({ upper: { end_date: '2026-03-12', end_price: 12.4 } })
  })

  it('merges edited and manually added labels into an isolated analysis copy', () => {
    const analysis = {
      run_id: 'run-1', as_of_date: '2026-08-18', completion_state: 'complete',
      stale: false, stale_reasons: [], warnings: [],
      items: [{ item_id: 'line-1', item_type: 'line' as const, payload: { first_price: 10 } }],
    }
    const labels: TrendReviewLabel[] = [
      { item_id: 'line-1', item_type: 'line', decision: 'accepted', rationale: '', payload: { first_price: 11 } },
      { item_id: 'manual-zone', item_type: 'zone', decision: 'accepted', rationale: '', payload: { lower: 9, upper: 10 } },
    ]

    const merged = mergeReviewLabelsIntoAnalysis(analysis, labels)

    expect(merged.items).toHaveLength(2)
    expect(merged.items[0].payload.first_price).toBe(11)
    expect(analysis.items[0].payload.first_price).toBe(10)
  })
})
