// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { projectChartSeries, settleChartSeries } from './chartSeriesProjection'
import { frontendPerformance, observeFrontend } from './frontendPerformance'
import { previousCloseByDate, type DailyBar } from './chartData'
import { resolveChartDrawingAnchor, resolveChartTradingIndex } from './chartDrawingInteraction'
import type { IChartApi } from 'lightweight-charts'

afterEach(() => vi.unstubAllGlobals())
const bars: DailyBar[] = [
  { trade_date: '2026-09-16', open: 10, high: 12, low: 9, close: 11, volume: 100, source: 'fixture', settlement: 0, open_interest: 0 },
  { trade_date: '2026-09-17', open: 11, high: 13, low: 10, close: 12, volume: 120, source: 'fixture' },
]

it('projects nullable futures fields and aggregation without changing raw bars', () => {
  const projection = projectChartSeries(bars, 1, previousCloseByDate(bars))
  expect(projection.settlements).toEqual([{ time: '2026-09-16', value: 0 }])
  expect(projection.openInterest[0].value).toBe(0)
  expect(projection.candles).toHaveLength(2)
  expect(projectChartSeries(bars, 2, previousCloseByDate(bars)).volumes[0].value).toBe(220)
  expect(bars[0].volume).toBe(100)
})

it('does not restore or report readiness for obsolete series generations', () => {
  const frames: FrameRequestCallback[] = []
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { frames.push(callback); return frames.length })
  let current = true
  const restore = vi.fn(), ready = vi.fn()
  const before = frontendPerformance()['chart-ready']?.count ?? 0
  settleChartSeries(performance.now(), () => current, restore, ready)
  frames.shift()!(0)
  current = false
  frames.shift()!(0)
  expect(restore).toHaveBeenCalledOnce()
  expect(ready).not.toHaveBeenCalled()
  expect(frontendPerformance()['chart-ready']?.count ?? 0).toBe(before)
  settleChartSeries(performance.now(), () => true, restore, ready)
  frames.shift()!(0)
  frames.shift()!(0)
  expect(ready).toHaveBeenCalledOnce()
  expect(frontendPerformance()['chart-ready'].count).toBe(before + 1)
})

it('keeps frontend counters bounded and copy isolated', () => {
  observeFrontend('chart-data', 10)
  const value = frontendPerformance()
  const count = value['chart-data'].count
  value['chart-data'].count = -1
  observeFrontend('chart-data', NaN)
  expect(frontendPerformance()['chart-data'].count).toBe(count)
  expect(Object.keys(frontendPerformance()).length).toBeLessThanOrEqual(2)
})

it('drawing interactions fail closed without geometry and map an aggregated period to its last date', () => {
  const rendered = projectChartSeries(bars, 2, previousCloseByDate(bars)).renderedBars
  expect(resolveChartDrawingAnchor(5, 5, null, null, rendered, bars)).toBeUndefined()
  const chart = { timeScale: () => ({ coordinateToLogical: () => 0 }) } as unknown as IChartApi
  expect(resolveChartTradingIndex(5, chart, rendered, bars)).toBe(1)
  expect(resolveChartTradingIndex(5, chart, [], bars)).toBeUndefined()
})
