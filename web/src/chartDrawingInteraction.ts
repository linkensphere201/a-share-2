import type { IChartApi, ISeriesApi } from 'lightweight-charts'
import { clamp, type DailyBar, type RenderBar } from './chartData'
import type { TrendLineAnchor } from './drawingStore'
import { barsInRenderPeriod, chooseAnchor } from './trendLines'

export function resolveChartDrawingAnchor(x: number, y: number, chart: IChartApi | null,
  candles: ISeriesApi<'Candlestick'> | null, rendered: RenderBar[], bars: DailyBar[]): TrendLineAnchor | undefined {
  if (!chart || !candles || !rendered.length) return undefined
  let nearest: RenderBar | undefined
  let nearestX = 0
  let distance = Infinity
  for (const period of rendered) {
    const coordinate = chart.timeScale().timeToCoordinate(period.trade_date)
    if (coordinate === null) continue
    if (Math.abs(coordinate - x) < distance) {
      nearest = period; nearestX = coordinate; distance = Math.abs(coordinate - x)
    }
  }
  const price = candles.coordinateToPrice(y)
  if (!nearest || price === null || !Number.isFinite(price)) return undefined
  const fallback: TrendLineAnchor = { date: nearest.trade_date, price, snap: 'free' }
  const candidates = barsInRenderPeriod(nearest, bars).flatMap(bar => {
    const highY = candles.priceToCoordinate(bar.high)
    const lowY = candles.priceToCoordinate(bar.low)
    return [
      ...(highY === null ? [] : [{ date: bar.trade_date, price: bar.high, snap: 'high' as const, x: nearestX, y: highY }]),
      ...(lowY === null ? [] : [{ date: bar.trade_date, price: bar.low, snap: 'low' as const, x: nearestX, y: lowY }]),
    ]
  })
  return chooseAnchor(x, y, fallback, candidates)
}

export function resolveChartTradingIndex(x: number, chart: IChartApi | null, rendered: RenderBar[], bars: DailyBar[]) {
  if (!chart || !bars.length || !rendered.length) return undefined
  const logical = chart.timeScale().coordinateToLogical(x)
  if (logical === null) return undefined
  const index = clamp(Math.round(Number(logical)), 0, rendered.length - 1)
  const nearestDate = rendered[index].trade_date
  const exact = bars.findIndex(bar => bar.trade_date === nearestDate)
  if (exact >= 0) return exact
  return bars.reduce((nearest, bar, i) => Math.abs(Date.parse(bar.trade_date) - Date.parse(nearestDate))
    < Math.abs(Date.parse(bars[nearest].trade_date) - Date.parse(nearestDate)) ? i : nearest, 0)
}
