import { describe, expect, it } from 'vitest'
import { assignGeneratedPatternLabels, projectGeneratedPatterns, projectGeneratedPivots, projectGeneratedTrendLines, projectGeneratedZones, readGeneratedBreakoutState } from './generatedAnalysisProjection'
import type { TrendAnalysisRun } from './trendAnalysisClient'
import type { IChartApi } from 'lightweight-charts'

const run: TrendAnalysisRun = {
  run_id: 'run-1',
  as_of_date: '2026-08-18',
  completion_state: 'complete',
  stale: false,
  stale_reasons: [],
  warnings: [],
  items: [
    { item_id: 'confirmed', item_type: 'anchor', payload: {
      kind: 'low', pivot_date: '2026-08-01', price: 10,
      confirmed_date: '2026-08-03', tentative: false,
    } },
    { item_id: 'tentative', item_type: 'anchor', payload: {
      kind: 'high', pivot_date: '2026-08-10', price: 12, tentative: true,
    } },
    { item_id: 'line-short', item_type: 'line', payload: {
      kind: 'support', horizon: 'short',
      first_pivot_date: '2026-08-01', first_price: 10,
      second_pivot_date: '2026-08-10', second_price: 12,
      score: 0.8, touch_count: 3,
    } },
    { item_id: 'key-level', item_type: 'zone', payload: {
      kind: 'key-level', lower: 9.9, upper: 10.1, score: 0.7,
    } },
    { item_id: 'volume-zone', item_type: 'zone', payload: {
      kind: 'estimated-volume-at-price', lower: 10.5, upper: 11,
      score: 0.2, estimated_share: 0.2,
    } },
    { item_id: 'double-bottom', item_type: 'pattern', payload: {
      display_name: '双底', completion_state: 'forming', neckline_price: 12,
      horizon: 'short', interpretation_rank: 1, primary: true, score: 0.75,
      pivots: [
        { pivot_date: '2026-08-01', price: 10 },
        { pivot_date: '2026-08-10', price: 12 },
        { pivot_date: '2026-08-01', price: 10.2 },
      ],
    } },
    { item_id: 'breakout-summary', item_type: 'evidence', parent_item_id: 'double-bottom', payload: {
      kind: 'breakout-state-summary', current_state: 'triggered', direction: 'up',
      boundary_price: 12, invalidation_level: 9.5, trigger_date: '2026-08-18',
      preview: true,
    } },
  ],
}

const chart = {
  timeScale: () => ({
    timeToCoordinate: (value: string) => value.endsWith('01') ? 20 : 80,
    width: () => 200,
  }),
  panes: () => [{ getHeight: () => 200 }],
} as unknown as IChartApi
const series = { priceToCoordinate: (price: number) => price * 10 }
const host = { clientWidth: 200, clientHeight: 200 } as HTMLDivElement

describe('generated analysis overlay projection', () => {
  it('reduces pattern label density and prioritizes the primary result', () => {
    const patterns = [
      { id: 'alternative', displayName: '候选', state: 'confirmed' as const, primary: false, points: '', neckline: { x1: 0, y1: 0, x2: 0, y2: 0 }, boundaries: [], labelX: 100, labelY: 20, showLabel: false, score: 0.95 },
      { id: 'primary', displayName: '主形态', state: 'forming' as const, primary: true, points: '', neckline: { x1: 0, y1: 0, x2: 0, y2: 0 }, boundaries: [], labelX: 110, labelY: 24, showLabel: false, score: 0.7 },
      { id: 'separate', displayName: '远端候选', state: 'confirmed' as const, primary: false, points: '', neckline: { x1: 0, y1: 0, x2: 0, y2: 0 }, boundaries: [], labelX: 300, labelY: 60, showLabel: false, score: 0.8 },
    ]

    expect(assignGeneratedPatternLabels(patterns, 360).filter(item => item.showLabel).map(item => item.id))
      .toEqual(['primary'])
    expect(assignGeneratedPatternLabels(patterns, 800).filter(item => item.showLabel).map(item => item.id))
      .toEqual(['primary', 'separate'])
  })
  it('projects date/price anchors independently from chart zoom pixels', () => {
    const result = projectGeneratedPivots(run, chart, series, host, true)

    expect(result).toEqual([
      expect.objectContaining({ id: 'confirmed', x: 20, y: 100, kind: 'low' }),
      expect.objectContaining({ id: 'tentative', x: 80, y: 120, tentative: true }),
    ])
  })

  it('hides only tentative anchors when configured', () => {
    expect(projectGeneratedPivots(run, chart, series, host, false).map(item => item.id))
      .toEqual(['confirmed'])
  })

  it('extends persisted lines and obeys independent horizon visibility', () => {
    const visible = projectGeneratedTrendLines(run, chart, series, host, true, false)

    expect(visible).toHaveLength(1)
    expect(visible[0]).toEqual(expect.objectContaining({
      id: 'line-short', kind: 'support', horizon: 'short', score: 0.8,
    }))
    expect(visible[0].line.x1).toBe(0)
    expect(projectGeneratedTrendLines(run, chart, series, host, false, true)).toEqual([])
  })

  it('projects medium-horizon lines through their independent visibility flag', () => {
    const medium: TrendAnalysisRun = {
      ...run,
      items: [...run.items, {
        item_id: 'line-medium', item_type: 'line', payload: {
          kind: 'resistance', horizon: 'medium',
          first_pivot_date: '2026-08-01', first_price: 12,
          second_pivot_date: '2026-08-10', second_price: 11,
          score: 0.9, touch_count: 3,
        },
      }],
    }

    expect(projectGeneratedTrendLines(medium, chart, series, host, false, false, undefined, true)
      .map(item => item.id)).toEqual(['line-medium'])
    expect(projectGeneratedTrendLines(medium, chart, series, host, false, false, undefined, false))
      .toEqual([])
  })

  it('projects only the highest-scoring line for each horizon and role', () => {
    const withCandidates: TrendAnalysisRun = {
      ...run,
      items: [...run.items, {
        item_id: 'line-short-weaker', item_type: 'line', payload: {
          kind: 'support', horizon: 'short',
          first_pivot_date: '2026-08-01', first_price: 9,
          second_pivot_date: '2026-08-10', second_price: 11,
          score: 0.6, touch_count: 5,
        },
      }, {
        item_id: 'line-short-resistance', item_type: 'line', payload: {
          kind: 'resistance', horizon: 'short',
          first_pivot_date: '2026-08-01', first_price: 12,
          second_pivot_date: '2026-08-10', second_price: 11,
          score: 0.7, touch_count: 2,
        },
      }],
    }

    expect(projectGeneratedTrendLines(withCandidates, chart, series, host, true, false).map(item => item.id))
      .toEqual(['line-short', 'line-short-resistance'])
  })

  it('adds the exact screener major line only when it is highlighted', () => {
    const withMajorLine: TrendAnalysisRun = {
      ...run,
      items: [...run.items, {
        item_id: 'major-line', item_type: 'line', payload: {
          kind: 'support', horizon: 'short', major_line_code: 'MDL-3M-01',
          first_pivot_date: '2026-08-01', first_price: 9,
          second_pivot_date: '2026-08-10', second_price: 11,
          score: 0.1, touch_count: 2,
        },
      }],
    }

    expect(projectGeneratedTrendLines(withMajorLine, chart, series, host, true, false).map(item => item.id))
      .toEqual(['line-short'])
    expect(projectGeneratedTrendLines(withMajorLine, chart, series, host, true, false, 'major-line'))
      .toEqual([expect.objectContaining({ id: 'line-short' }), expect.objectContaining({ id: 'major-line', label: 'MDL-3M-01' })])
  })

  it('projects key levels and estimated volume zones only into the price pane', () => {
    const zones = projectGeneratedZones(run, chart, series, host, true, true)

    expect(zones.map(item => item.kind)).toEqual([
      'key-level', 'estimated-volume-at-price',
    ])
    expect(zones[0]).toEqual(expect.objectContaining({ y: 99, height: 2, width: 200 }))
    expect(projectGeneratedZones(run, chart, series, host, true, false).map(item => item.id))
      .toEqual(['key-level'])
  })

  it('projects persisted pattern pivots and neckline with visibility isolation', () => {
    const patterns = projectGeneratedPatterns(run, chart, series, true)

    expect(patterns).toHaveLength(1)
    expect(patterns[0]).toEqual(expect.objectContaining({
      id: 'double-bottom', displayName: '双底', state: 'forming', primary: true,
      boundaries: [],
    }))
    expect(patterns[0].neckline).toEqual({ x1: 20, y1: 120, x2: 80, y2: 120 })
    expect(projectGeneratedPatterns(run, chart, series, false)).toEqual([])
  })

  it('projects only the primary active pattern for each horizon', () => {
    const pattern = run.items.find(item => item.item_id === 'double-bottom')!
    const withCandidates: TrendAnalysisRun = {
      ...run,
      items: [
        ...run.items,
        { ...pattern, item_id: 'short-alternative', payload: {
          ...pattern.payload, horizon: 'short', primary: false,
          interpretation_rank: 2, completion_state: 'confirmed',
        } },
        { ...pattern, item_id: 'long-primary', payload: {
          ...pattern.payload, horizon: 'long', primary: true,
          interpretation_rank: 1, completion_state: 'confirmed',
        } },
        { ...pattern, item_id: 'long-invalidated', payload: {
          ...pattern.payload, horizon: 'long', primary: false,
          interpretation_rank: 2, completion_state: 'invalidated',
        } },
      ],
    }

    expect(projectGeneratedPatterns(withCandidates, chart, series, true).map(item => item.id))
      .toEqual(['double-bottom', 'long-primary'])
  })

  it('keeps consolidation boundaries within the detected pattern interval', () => {
    const consolidation: TrendAnalysisRun = {
      ...run,
      items: [{
        item_id: 'triangle', item_type: 'pattern', payload: {
          display_name: '对称三角形', completion_state: 'forming', neckline_price: 11,
          pivots: [
            { pivot_date: '2026-08-01', price: 10 },
            { pivot_date: '2026-08-10', price: 12 },
            { pivot_date: '2026-08-01', price: 10.5 },
          ],
          boundary_geometry: {
            upper: {
              start_date: '2026-08-01', start_price: 13,
              end_date: '2026-08-10', end_price: 12,
            },
            lower: {
              start_date: '2026-08-01', start_price: 9,
              end_date: '2026-08-10', end_price: 10,
            },
          },
        },
      }],
    }

    const pattern = projectGeneratedPatterns(consolidation, chart, series, true)[0]

    expect(pattern.boundaries).toHaveLength(2)
    expect(pattern.boundaries[0].x2).toBe(80)
  })

  it('projects all four broadening and contracting diamond segments', () => {
    const segment = {
      start_date: '2026-08-01', start_price: 10,
      end_date: '2026-08-10', end_price: 12,
    }
    const diamond: TrendAnalysisRun = {
      ...run,
      items: [{
        item_id: 'diamond', item_type: 'pattern', payload: {
          display_name: '菱形', completion_state: 'forming', neckline_price: 11,
          pivots: [
            { pivot_date: '2026-08-01', price: 10 },
            { pivot_date: '2026-08-10', price: 12 },
            { pivot_date: '2026-08-01', price: 10.5 },
          ],
          boundary_geometry: { segments: [segment, segment, segment, segment] },
        },
      }],
    }

    expect(projectGeneratedPatterns(diamond, chart, series, true)[0].boundaries)
      .toHaveLength(4)
  })

  it('projects a one-pivot V formation through its two boundary segments', () => {
    const vPattern: TrendAnalysisRun = {
      ...run,
      items: [{
        item_id: 'v-bottom', item_type: 'pattern', payload: {
          display_name: 'V形底', completion_state: 'confirmed', neckline_price: 12,
          pivots: [{ pivot_date: '2026-08-10', price: 9 }],
          boundary_geometry: { segments: [
            {
              start_date: '2026-08-01', start_price: 12,
              end_date: '2026-08-10', end_price: 9,
            },
            {
              start_date: '2026-08-10', start_price: 9,
              end_date: '2026-08-18', end_price: 12,
            },
          ] },
        },
      }],
    }

    const projected = projectGeneratedPatterns(vPattern, chart, series, true)

    expect(projected).toHaveLength(1)
    expect(projected[0].boundaries).toHaveLength(2)
    expect(projected[0].displayName).toBe('V形底')
  })

  it('reads the primary pattern breakout summary without mixing layer visibility', () => {
    expect(readGeneratedBreakoutState(run, true)).toEqual({
      state: 'triggered', direction: 'up', boundaryPrice: 12,
      invalidationPrice: 9.5, triggerDate: '2026-08-18',
      confirmationDate: undefined, failureDate: undefined, preview: true,
    })
    expect(readGeneratedBreakoutState(run, false)).toBeUndefined()
  })

  it('prioritizes a latest non-neutral line or level event over an older pattern state', () => {
    const withLatestEvent: TrendAnalysisRun = {
      ...run,
      items: [...run.items, {
        item_id: 'line-event', item_type: 'evidence', parent_item_id: 'line-short',
        payload: {
          kind: 'latest-structural-event-summary', current_state: 'failed',
          event_kind: 'false-breakout-risk',
          direction: 'down', boundary_price: 10.2, invalidation_level: 10.2,
          failure_date: '2026-08-18', preview: true,
        },
      }],
    }

    expect(readGeneratedBreakoutState(withLatestEvent, true)).toEqual(expect.objectContaining({
      state: 'failed', direction: 'down', failureDate: '2026-08-18', preview: true,
      eventKind: 'false-breakout-risk',
    }))
  })
})
