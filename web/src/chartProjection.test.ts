import { describe, expect, it } from 'vitest'
import type { IChartApi, ISeriesApi } from 'lightweight-charts'
import { projectMeasurement, projectTrendLineAnchors } from './chartProjection'
import type { RenderBar } from './chartData'

const coordinates: Record<string, number> = {
  '2026-08-01': 20,
  '2026-08-02': 40,
  '2026-08-03': 60,
}

const chart = {
  timeScale: () => ({
    timeToCoordinate: (time: string) => coordinates[time] ?? null,
    width: () => 400,
  }),
  panes: () => [{ getHeight: () => 200 }],
} as unknown as IChartApi

const candles = {
  priceToCoordinate: (price: number) => 200 - price * 10,
} as unknown as ISeriesApi<'Candlestick'>

describe('chart projections', () => {
  it('projects immutable trend-line anchors through current time and price scales', () => {
    const periods: RenderBar[] = [
      { trade_date: '2026-08-02', period_start: '2026-08-01', open: 10, high: 12, low: 9, close: 11, volume: 1, source: 'test' },
      { trade_date: '2026-08-03', period_start: '2026-08-03', open: 11, high: 13, low: 10, close: 12, volume: 1, source: 'test' },
    ]

    expect(projectTrendLineAnchors([
      { date: '2026-08-01', price: 10, snap: 'high' },
      { date: '2026-08-03', price: 12, snap: 'low' },
    ], periods, chart, candles)).toEqual({ x1: 40, y1: 100, x2: 60, y2: 80 })
  })

  it('projects measurement anchors and keeps the label inside the pane', () => {
    const geometry = projectMeasurement({
      from: '2026-08-01',
      to: '2026-08-03',
      startAnchor: '2026-08-01',
      endAnchor: '2026-08-03',
      open: 10,
      close: 12,
      changePercent: 20,
      elapsedDays: 2,
      kLineCount: 3,
    }, chart, candles, { clientWidth: 400, clientHeight: 200 } as HTMLDivElement)

    expect(geometry).toMatchObject({ width: 400, height: 200, startX: 20, startY: 100, endX: 60, endY: 80 })
    expect(geometry!.labelLeft).toBeGreaterThanOrEqual(8)
    expect(geometry!.labelTop).toBeGreaterThanOrEqual(36)
  })
})
