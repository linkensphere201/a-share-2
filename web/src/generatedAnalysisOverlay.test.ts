import { describe, expect, it } from 'vitest'
import { projectGeneratedPivots } from './ChartCanvas'
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
  ],
}

const chart = {
  timeScale: () => ({ timeToCoordinate: (value: string) => value.endsWith('01') ? 20 : 80 }),
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
})
