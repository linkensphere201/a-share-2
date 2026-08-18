import { expect, it } from 'vitest'
import { readTrendEvidence } from './trendEvidence'
import type { TrendAnalysisRun } from './trendAnalysisClient'

it('builds auditable pattern, price-volume, context, and warning evidence', () => {
  const run: TrendAnalysisRun = {
    run_id: 'run', as_of_date: '2026-08-18', completion_state: 'partial',
    source_observed_at_ms: 123, stale: true, stale_reasons: ['new canonical bar'],
    warnings: [{ message: 'board history incomplete' }],
    items: [
      { item_id: 'pattern', item_type: 'pattern', payload: {
        primary: true, display_name: '双底', timeframe: 'daily', score: 0.8,
        score_components: { symmetry: 0.9, volume: 0.7 },
      } },
      { item_id: 'event', item_type: 'evidence', parent_item_id: 'pattern', payload: {
        kind: 'latest-structural-event-summary', current_state: 'triggered',
        evidence: { distance_percent: 0.02, relative_volume: 1.5, close_location: 0.8 },
      } },
      { item_id: 'market-board-context-evidence', item_type: 'evidence', payload: {
        alignment: 'supportive', combined_score: 0.42, available_context_count: 3,
      } },
    ],
  }

  const result = readTrendEvidence(run, {
    state: 'triggered', direction: 'up', boundaryPrice: 12,
    invalidationPrice: 9.5, preview: true, eventKind: 'upward-breakout',
  })

  expect(result).toMatchObject({
    source: 'preview', patternName: '双底', score: 0.8,
    boundaryPrice: 12, invalidationPrice: 9.5,
    context: { alignment: 'supportive', score: 0.42, availableCount: 3 },
  })
  expect(result?.scoreComponents).toHaveLength(2)
  expect(result?.observation).toContainEqual({ label: '距离', value: '2.0%' })
  expect(result?.warnings).toEqual(['new canonical bar', 'board history incomplete'])
})

it('returns a valid sparse view when no pattern or context exists', () => {
  const run: TrendAnalysisRun = {
    run_id: 'run', as_of_date: '2026-08-18', completion_state: 'complete',
    stale: false, stale_reasons: [], warnings: [], items: [],
  }

  expect(readTrendEvidence(run)).toMatchObject({
    source: 'official', timeframe: 'daily', scoreComponents: [], warnings: [],
  })
})
