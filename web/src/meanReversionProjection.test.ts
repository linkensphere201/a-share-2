import { describe, expect, it } from 'vitest'
import { projectMeanReversion } from './meanReversionProjection'
import type { AnalysisChartProjection } from './signalReviewClient'

describe('mean reversion chart projection', () => {
  it('projects center, ATR band, confirmation, invalidation and target levels', () => {
    const projections: AnalysisChartProjection[] = [{
      projection_id: 'mr:center', kind: 'series-line', role: 'moving-center',
      label: 'EMA20运动中心',
      points: [{ date: '2026-09-10', price: 10 }, { date: '2026-09-11', price: 11 }],
    }, {
      projection_id: 'mr:deviation-band', kind: 'series-band', role: 'atr-deviation-band',
      label: 'ATR偏离带',
      upper_points: [{ date: '2026-09-10', price: 12 }, { date: '2026-09-11', price: 13 }],
      lower_points: [{ date: '2026-09-10', price: 8 }, { date: '2026-09-11', price: 9 }],
    }, ...(['confirmation', 'invalidation', 'target'] as const).map((role, index) => ({
      projection_id: `mr:${role}`, kind: 'price-line' as const, role,
      label: role, price: 9 + index,
    }))]
    const chart = {
      timeScale: () => ({ timeToCoordinate: (time: string) => time.endsWith('10') ? 10 : 20 }),
    }
    const series = { priceToCoordinate: (price: number) => 200 - price * 10 }
    const host = { clientWidth: 640 }

    const result = projectMeanReversion(
      projections, chart as never, series, host as HTMLDivElement,
    )

    expect(result?.lines[0].points).toBe('10,100 20,90')
    expect(result?.bands[0].polygon).toBe('10,80 20,70 20,110 10,120')
    expect(result?.levels.map(item => item.role)).toEqual([
      'confirmation', 'invalidation', 'target',
    ])
    expect(result?.levels.every(item => item.width === 640)).toBe(true)
  })
})
