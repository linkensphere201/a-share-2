import { expect, it } from 'vitest'
import { parallelReviewSystems } from './parallelReviewSystems'
import type { SignalScoreResult } from './signalReviewClient'

const score = (system: string, symbol: string, total: number, eligible: boolean) => ({
  system_id: system, symbol, entity_scope: 'board', total_score: total, eligible,
  setup_family: system === 'mean-reversion' ? 'oversold-exhaustion' : 'breakout',
}) as SignalScoreResult

it('admits mean-only opportunities without trend eligibility and ranks independently', () => {
  const result = parallelReviewSystems([
    score('trend-breakout', 'A', 90, true), score('trend-breakout', 'B', 50, false),
    score('mean-reversion', 'B', 80, true), score('mean-reversion', 'A', 70, false),
  ], 'opportunities', 'board', new Set(), new Set())
  expect(result.trend.map(s => s.symbol)).toEqual(['A'])
  expect(result.mean.map(s => s.symbol)).toEqual(['B'])
})

it('limits both radar rankings to discovered symbols, not to trend eligibility', () => {
  const result = parallelReviewSystems([
    score('trend-breakout', 'A', 50, false), score('mean-reversion', 'A', 80, true),
    score('mean-reversion', 'B', 95, true),
  ], 'leading', 'board', new Set(), new Set(['A']))
  expect(result.trend.map(s => s.symbol)).toEqual(['A'])
  expect(result.mean.map(s => s.symbol)).toEqual(['A'])
})

it('includes independently eligible mean candidates outside a legacy trend pool', () => {
  const result = parallelReviewSystems([
    score('trend-breakout', 'A', 50, false), score('mean-reversion', 'B', 80, true),
  ], 'board-pool', 'board', new Set(['A']), new Set())
  expect(result.mean.map(s => s.symbol)).toEqual(['B'])
})
