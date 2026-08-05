import type { IChartApi, IPaneApi, ISeriesApi, Time } from 'lightweight-charts'
import type { TrendLineAnchor, TrendLineDrawing } from './drawingStore'
import {
  clamp,
  visibleExtrema,
  visibleUnfilledPriceGaps,
  type DailyBar,
  type PriceGap,
  type RangeMeasurement,
  type RenderBar,
} from './chartData'
import { extendLineToBounds, renderDateForAnchor, type LineGeometry } from './trendLines'

export type ProjectedTrendLine = {
  drawing: TrendLineDrawing
  line: LineGeometry
  anchors: LineGeometry
}

export type PricePointGeometry = {
  kind: 'high' | 'low'
  x: number
  y: number
  price: number
}

export type GapGeometry = PriceGap & {
  x1: number
  x2: number
  y1: number
  y2: number
}

export type MarketAnnotationGeometry = {
  width: number
  height: number
  extrema: PricePointGeometry[]
  gaps: GapGeometry[]
}

export type MeasurementGeometry = {
  width: number
  height: number
  startX: number
  startY: number
  endX: number
  endY: number
  labelLeft: number
  labelTop: number
}

export function projectPaneTop(
  chart: IChartApi | null,
  pane: IPaneApi<Time> | null,
): number | undefined {
  if (!chart || !pane) return undefined
  const panes = chart.panes()
  const paneIndex = panes.indexOf(pane)
  if (paneIndex < 0) return undefined
  return panes.slice(0, paneIndex).reduce((top, item) => top + item.getHeight(), 0)
}

export function projectTrendLines(
  drawings: TrendLineDrawing[],
  periods: RenderBar[],
  chart: IChartApi | null,
  candles: ISeriesApi<'Candlestick'> | null,
): ProjectedTrendLine[] {
  if (!chart || !candles) return []
  const width = chart.timeScale().width()
  const height = chart.panes()[0]?.getHeight() ?? 0
  return drawings.filter(drawing => drawing.visible).flatMap(drawing => {
    const projected = projectTrendLineAnchors(drawing.anchors, periods, chart, candles)
    return projected ? [{ drawing, anchors: projected, line: extendLineToBounds(projected, width, height) }] : []
  })
}

export function projectTrendLineAnchors(
  anchors: [TrendLineAnchor, TrendLineAnchor],
  periods: RenderBar[],
  chart: IChartApi | null,
  candles: ISeriesApi<'Candlestick'> | null,
): LineGeometry | undefined {
  if (!chart || !candles) return undefined
  const firstDate = renderDateForAnchor(anchors[0].date, periods)
  const secondDate = renderDateForAnchor(anchors[1].date, periods)
  if (!firstDate || !secondDate) return undefined
  const x1 = chart.timeScale().timeToCoordinate(firstDate)
  const x2 = chart.timeScale().timeToCoordinate(secondDate)
  const y1 = candles.priceToCoordinate(anchors[0].price)
  const y2 = candles.priceToCoordinate(anchors[1].price)
  if (x1 === null || x2 === null || y1 === null || y2 === null) return undefined
  return { x1, y1, x2, y2 }
}

export function projectMeasurement(
  measurement: RangeMeasurement | undefined,
  chart: IChartApi | null,
  candles: ISeriesApi<'Candlestick'> | null,
  host: HTMLDivElement | null,
): MeasurementGeometry | undefined {
  if (!measurement || !chart || !candles || !host) return undefined
  const startX = chart.timeScale().timeToCoordinate(measurement.startAnchor)
  const endX = chart.timeScale().timeToCoordinate(measurement.endAnchor)
  const startY = candles.priceToCoordinate(measurement.open)
  const endY = candles.priceToCoordinate(measurement.close)
  if (startX === null || endX === null || startY === null || endY === null) return undefined
  const height = chart.panes()[0]?.getHeight() ?? host.clientHeight
  const labelWidth = Math.min(168, Math.max(132, host.clientWidth - 16))
  const midpointX = (startX + endX) / 2
  const midpointY = (startY + endY) / 2
  return {
    width: host.clientWidth,
    height,
    startX,
    startY,
    endX,
    endY,
    labelLeft: clamp(midpointX + 8, 8, Math.max(8, host.clientWidth - labelWidth - 8)),
    labelTop: clamp(midpointY - 24, 36, Math.max(36, height - 66)),
  }
}

export function projectMarketAnnotations(
  renderedBars: RenderBar[],
  priceGaps: PriceGap[],
  chart: IChartApi | null,
  candles: ISeriesApi<'Candlestick'> | null,
  host: HTMLDivElement | null,
  lodBucket: number,
): MarketAnnotationGeometry | undefined {
  if (!chart || !candles || !host || renderedBars.length === 0) return undefined
  const visible = chart.timeScale().getVisibleRange()
  if (!visible) return undefined
  const from = String(visible.from)
  const to = String(visible.to)
  const extrema = visibleExtrema(renderedBars, from, to)
  if (!extrema) return undefined
  const height = chart.panes()[0]?.getHeight() ?? host.clientHeight
  const projectPoint = (kind: 'high' | 'low', bar: DailyBar): PricePointGeometry | undefined => {
    const x = chart.timeScale().timeToCoordinate(bar.trade_date)
    const price = kind === 'high' ? bar.high : bar.low
    const y = candles.priceToCoordinate(price)
    return x === null || y === null ? undefined : { kind, x, y, price }
  }
  const projectedExtrema = [
    projectPoint('high', extrema.high),
    projectPoint('low', extrema.low),
  ].filter((item): item is PricePointGeometry => item !== undefined)
  const visibleBars = renderedBars.filter(bar => bar.trade_date >= from && bar.trade_date <= to)
  const lastVisibleDate = visibleBars.at(-1)?.trade_date
  const gaps = lodBucket === 1 && lastVisibleDate
    ? visibleUnfilledPriceGaps(priceGaps, to).flatMap(gap => {
        const effectiveEnd = gap.fillDate ?? lastVisibleDate
        const startDate = gap.startDate < from ? visibleBars.at(0)?.trade_date : gap.startDate
        const endDate = effectiveEnd > to ? lastVisibleDate : effectiveEnd
        if (!startDate || !endDate) return []
        const startX = chart.timeScale().timeToCoordinate(startDate)
        const endX = chart.timeScale().timeToCoordinate(endDate)
        const upperY = candles.priceToCoordinate(gap.upper)
        const lowerY = candles.priceToCoordinate(gap.lower)
        if (startX === null || endX === null || upperY === null || lowerY === null) return []
        return [{
          ...gap,
          x1: Math.min(startX, endX),
          x2: Math.max(startX + 1, endX),
          y1: upperY,
          y2: lowerY,
        }]
      })
    : []
  return { width: host.clientWidth, height, extrema: projectedExtrema, gaps }
}
