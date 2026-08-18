import { describe, expect, it } from 'vitest'
import { projectGeneratedPatterns, projectGeneratedPivots, projectGeneratedTrendLines, projectGeneratedZones } from './ChartCanvas'
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
      primary: true, score: 0.75,
      pivots: [
        { pivot_date: '2026-08-01', price: 10 },
        { pivot_date: '2026-08-10', price: 12 },
        { pivot_date: '2026-08-01', price: 10.2 },
      ],
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
    }))
    expect(patterns[0].neckline).toEqual({ x1: 20, y1: 120, x2: 200, y2: 120 })
    expect(projectGeneratedPatterns(run, chart, series, false)).toEqual([])
  })
})
