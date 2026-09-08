import { clamp, type NumericRange } from './chartData'

export type ChartViewport = {
  logical: NumericRange
  price: NumericRange
}

function transformPrice(value: number, logarithmic: boolean): number {
  return logarithmic ? Math.log(Math.max(value, Number.EPSILON)) : value
}

function restorePrice(value: number, logarithmic: boolean): number {
  return logarithmic ? Math.exp(value) : value
}

function mapPriceRange(
  range: NumericRange,
  logarithmic: boolean,
  transform: (from: number, to: number) => NumericRange,
): NumericRange {
  const mapped = transform(
    transformPrice(range.from, logarithmic),
    transformPrice(range.to, logarithmic),
  )
  const restored = {
    from: restorePrice(mapped.from, logarithmic),
    to: restorePrice(mapped.to, logarithmic),
  }
  if (logarithmic || restored.from > 0) return restored
  const shift = Number.EPSILON - restored.from
  return { from: restored.from + shift, to: restored.to + shift }
}

export function panChartViewport(
  viewport: ChartViewport,
  deltaX: number,
  deltaY: number,
  plotWidth: number,
  plotHeight: number,
  logarithmic = false,
): ChartViewport {
  const logicalSpan = viewport.logical.to - viewport.logical.from
  const logicalOffset = logicalSpan * deltaX / Math.max(1, plotWidth)
  const price = mapPriceRange(viewport.price, logarithmic, (from, to) => {
    const offset = (to - from) * deltaY / Math.max(1, plotHeight)
    return { from: from + offset, to: to + offset }
  })
  return {
    logical: {
      from: viewport.logical.from - logicalOffset,
      to: viewport.logical.to - logicalOffset,
    },
    price,
  }
}

export function zoomChartViewport(
  viewport: ChartViewport,
  factor: number,
  anchorXFraction: number,
  anchorYFraction: number,
  logarithmic = false,
): ChartViewport {
  const boundedFactor = Math.max(0.01, factor)
  const x = clamp(anchorXFraction, 0, 1)
  const y = clamp(anchorYFraction, 0, 1)
  const logicalAnchor = viewport.logical.from
    + (viewport.logical.to - viewport.logical.from) * x
  const logical = {
    from: logicalAnchor - (logicalAnchor - viewport.logical.from) * boundedFactor,
    to: logicalAnchor + (viewport.logical.to - logicalAnchor) * boundedFactor,
  }
  const price = mapPriceRange(viewport.price, logarithmic, (from, to) => {
    const anchor = from + (to - from) * (1 - y)
    return {
      from: anchor - (anchor - from) * boundedFactor,
      to: anchor + (to - anchor) * boundedFactor,
    }
  })
  return { logical, price }
}

export function constrainLogicalViewport(
  range: NumericRange,
  dataCount: number,
  minimumDataOccupancy = 0.18,
  minimumVisibleBars = 5,
  maximumBlankFraction = 0.42,
): NumericRange {
  if (dataCount <= 0) return range
  const dataSpan = Math.max(1, dataCount - 1)
  const span = clamp(
    range.to - range.from,
    Math.min(minimumVisibleBars, dataSpan),
    dataSpan / clamp(minimumDataOccupancy, 0.05, 1),
  )
  const center = clamp(
    (range.from + range.to) / 2,
    -span * maximumBlankFraction,
    dataSpan + span * maximumBlankFraction,
  )
  return { from: center - span / 2, to: center + span / 2 }
}

export function constrainPriceViewport(
  range: NumericRange,
  dataLow: number,
  dataHigh: number,
  logarithmic = false,
  minimumDataOccupancy = 0.18,
  minimumSpanFraction = 0.001,
  maximumBlankFraction = 0.42,
  visibleLow = dataLow,
  visibleHigh = dataHigh,
): NumericRange {
  const low = transformPrice(Math.min(dataLow, dataHigh), logarithmic)
  const high = transformPrice(Math.max(dataLow, dataHigh), logarithmic)
  const dataSpan = Math.max(Number.EPSILON, high - low)
  const focusLow = transformPrice(Math.min(visibleLow, visibleHigh), logarithmic)
  const focusHigh = transformPrice(Math.max(visibleLow, visibleHigh), logarithmic)
  const focusSpan = Math.max(Number.EPSILON, focusHigh - focusLow)
  return mapPriceRange(range, logarithmic, (from, to) => {
    const maximumSpan = focusSpan / clamp(minimumDataOccupancy, 0.05, 1)
    const span = clamp(
      to - from,
      Math.min(dataSpan * minimumSpanFraction, maximumSpan),
      maximumSpan,
    )
    const center = clamp(
      (from + to) / 2,
      focusLow - span * maximumBlankFraction,
      focusHigh + span * maximumBlankFraction,
    )
    return { from: center - span / 2, to: center + span / 2 }
  })
}

export function wheelZoomFactor(deltaY: number, intensity = 0.0005): number {
  return Math.exp(clamp(deltaY, -10_000, 10_000) * intensity)
}
