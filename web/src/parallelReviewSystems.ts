import type { SignalScoreResult } from './signalReviewClient'

export type ReviewModule = 'results' | 'opportunities' | 'leading' | 'hotspots' | 'observations' | 'board-pool' | 'stock-pool'

export function parallelReviewSystems(
  scores: SignalScoreResult[], module: ReviewModule, scope: 'market' | 'board' | 'stock',
  memberSymbols: Set<string>, radarSymbols: Set<string>,
) {
  const actualScope = module === 'stock-pool' ? 'stock'
    : ['board-pool', 'leading', 'hotspots', 'observations'].includes(module) ? 'board' : scope
  const candidates = scores.filter(s => s.entity_scope === actualScope)
  const rank = (values: SignalScoreResult[]) => values.filter(s => {
    if (module === 'leading' || module === 'hotspots') return radarSymbols.has(s.symbol)
    if (module === 'opportunities') return s.eligible
    if (module === 'board-pool' || module === 'stock-pool') return memberSymbols.has(s.symbol) || s.eligible
    return true
  }).sort((a, b) => Number(b.eligible) - Number(a.eligible)
    || b.total_score - a.total_score || a.symbol.localeCompare(b.symbol))
  return {
    trend: rank(candidates.filter(s => s.system_id === (actualScope === 'stock'
      ? 'stock-trend-opportunity' : actualScope === 'market' ? 'market-regime' : 'trend-breakout'))),
    mean: rank(candidates.filter(s => s.system_id === 'mean-reversion' && s.setup_family !== 'none')),
  }
}
