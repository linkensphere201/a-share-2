import { describe, expect, it } from 'vitest'
import { buildTrendExplanation } from './trendExplanation'
import type { TrendAnalysisRun } from './trendAnalysisClient'

const basePattern = {
  display_name: '双底', completion_state: 'confirmed', horizon: 'short',
  start_date: '2026-06-01', end_date: '2026-07-01', neckline_price: 12,
  pivots: [], score: 0.8,
}

describe('trend explanation', () => {
  it('summarizes only the same core candidates used by the chart overlay', () => {
    const run: TrendAnalysisRun = {
      run_id: 'run', as_of_date: '2026-08-21', completion_state: 'complete',
      stale: false, stale_reasons: [], warnings: [], items: [
        line('short-support-best', 'short', 'support', 0.9),
        line('short-support-weaker', 'short', 'support', 0.7),
        line('long-resistance', 'long', 'resistance', 0.8),
        structuralEvent('short-support-best', 'no-structural-change', 'ready', 'down', 11.25),
        structuralEvent('long-resistance', 'no-structural-change', 'ready', 'up', 18.5),
        zone('key-best', 'key-level', 0.9),
        zone('key-second', 'key-level', 0.8),
        zone('key-third', 'key-level', 0.7),
        { item_id: 'short-primary', item_type: 'pattern', payload: {
          ...basePattern, primary: true, interpretation_rank: 1,
        } },
        { item_id: 'short-alternative', item_type: 'pattern', payload: {
          ...basePattern, primary: false, interpretation_rank: 2,
        } },
      ],
    }

    const result = buildTrendExplanation(run)!

    expect(result.sections.find(value => value.id === 'trend-lines')?.items.map(value => value.analysisItemId))
      .toEqual(['short-support-best', 'long-resistance'])
    expect(result.sections.find(value => value.id === 'key-levels')?.items.map(value => value.analysisItemId))
      .toEqual(['key-best', 'key-second'])
    expect(result.sections.find(value => value.id === 'patterns')?.items.map(value => value.analysisItemId))
      .toEqual(['short-primary'])
    expect(result.sections.find(value => value.id === 'breakout-state')?.items.map(value => value.title))
      .toEqual(['短期支撑：尚未向下破位', '长期压力：尚未向上突破'])
    expect(result.sections.find(value => value.id === 'trend-lines')?.items[0].detail)
      .toContain('尚未向下破位')
    expect(result.sections.find(value => value.id === 'trend-lines')?.items[0].detail)
      .toContain('独立确认 1 次；形成期最大越界 0.20 ATR')
  })

  it('describes the latest structural event without exposing provider prose', () => {
    const run: TrendAnalysisRun = {
      run_id: 'run', as_of_date: '2026-08-21', completion_state: 'complete',
      stale: false, stale_reasons: [], warnings: [{ code: 'adjustment_factors_incomplete' }], items: [
        line('line', 'short', 'resistance', 0.9), {
        item_id: 'event', item_type: 'evidence', parent_item_id: 'line', payload: {
          kind: 'latest-structural-event-summary', event_kind: 'upward-breakout',
          current_state: 'triggered', direction: 'up', event_date: '2026-08-21', boundary_price: 12.34,
          reason: 'raw internal reason',
        },
      }],
    }

    const result = buildTrendExplanation(run)
    expect(result?.summary).toBe('最新日线形成向上突破，参考边界 12.34。')
    expect(result?.sections.find(value => value.id === 'breakout-state')?.items[0].title)
      .toBe('短期压力：已向上突破')
    expect(result?.warnings).toEqual(['复权因子不完整，当前分析采用未复权价格。'])
  })

  it('explains moving-average convergence with compact auditable metrics', () => {
    const run: TrendAnalysisRun = {
      run_id: 'run', as_of_date: '2026-08-21', completion_state: 'complete',
      stale: false, stale_reasons: [], warnings: [], items: [{
        item_id: 'ma', item_type: 'pattern', payload: {
          pattern_type: 'moving-average-convergence', display_name: '均线粘合向上发散',
          completion_state: 'confirmed', horizon: 'medium', start_date: '2026-08-01',
          end_date: '2026-08-21', neckline_price: 10.2, pivots: [], score: 0.82,
          ma_periods: [5, 20, 60], spread_percent: 0.0123, spread_atr: 0.71,
          contraction_ratio: 0.64, compressed_bars: 7, volume_ratio: 1.56,
          primary: true, interpretation_rank: 1,
        },
      }],
    }

    const item = buildTrendExplanation(run)?.sections.find(value => value.id === 'patterns')?.items[0]
    expect(item?.title).toBe('中期均线粘合向上发散')
    expect(item?.detail).toContain('MA5/MA20/MA60')
    expect(item?.detail).toContain('离散度 1.23% / 0.71 ATR')
    expect(item?.detail).toContain('持续 7 根；收敛率 0.64；量比 1.56')
  })
})

function line(id: string, horizon: string, kind: string, score: number) {
  return { item_id: id, item_type: 'line' as const, payload: {
    horizon, kind, score, first_pivot_date: '2026-06-01', first_price: 10,
    second_pivot_date: '2026-07-01', second_price: 11,
    projected_price: 12, touch_count: 3, independent_touch_count: 1,
    maximum_breach_atr: 0.2, slope_per_bar: 0.1,
  } }
}

function zone(id: string, kind: string, score: number) {
  return { item_id: id, item_type: 'zone' as const, payload: {
    kind, score, lower: 10, upper: 11, observation_count: 4,
  } }
}

function structuralEvent(parent: string, eventKind: string, state: string, direction: string, boundary: number) {
  return { item_id: `${parent}-event`, item_type: 'evidence' as const, parent_item_id: parent, payload: {
    kind: 'latest-structural-event-summary', event_kind: eventKind,
    current_state: state, direction, event_date: '2026-08-21', boundary_price: boundary,
  } }
}
