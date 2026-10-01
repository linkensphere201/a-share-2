import type { CandlestickData, HistogramData, LineData, Time } from 'lightweight-charts'
import { aggregateBars, candleColor, type DailyBar } from './chartData'
import { observeFrontend } from './frontendPerformance'

export function projectChartSeries(bars: DailyBar[], bucket: number, previousClose: Map<string, number>) {
  const renderedBars = aggregateBars(bars, bucket)
  const colors = new Map(renderedBars.map(item => [item.trade_date, candleColor(item, previousClose.get(item.period_start))]))
  const candles: CandlestickData<Time>[] = renderedBars.map(item => ({
    time: item.trade_date, open: item.open, high: item.high, low: item.low, close: item.close,
    color: colors.get(item.trade_date), borderColor: colors.get(item.trade_date), wickColor: colors.get(item.trade_date),
  }))
  const volumes: HistogramData<Time>[] = renderedBars.map(item => ({
    time: item.trade_date, value: item.volume, color: `${colors.get(item.trade_date)!}99`,
  }))
  const closes: LineData<Time>[] = renderedBars.map(item => ({ time: item.trade_date, value: item.close }))
  const settlements: LineData<Time>[] = renderedBars.flatMap(item => item.settlement == null
    ? [] : [{ time: item.trade_date, value: item.settlement }])
  const openInterest: HistogramData<Time>[] = renderedBars.flatMap(item => item.open_interest == null
    ? [] : [{ time: item.trade_date, value: item.open_interest, color: '#4f91b8aa' }])
  return { renderedBars, candles, volumes, closes, settlements, openInterest,
    times: new Set(renderedBars.map(item => item.trade_date)) }
}

/** The chart library settles scales on a later frame; obsolete generations do nothing. */
export function settleChartSeries(startedAt: number, current: () => boolean, restore: () => void, ready: () => void) {
  window.requestAnimationFrame(() => {
    if (!current()) return
    restore()
    window.requestAnimationFrame(() => {
      if (!current()) return
      ready()
      observeFrontend('chart-ready', performance.now() - startedAt)
    })
  })
}
