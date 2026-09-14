import type { IChartApi, Time } from 'lightweight-charts'
import type { AnalysisChartProjection } from './signalReviewClient'

export type MeanReversionSeriesGeometry = {
  id: string
  role: AnalysisChartProjection['role']
  label: string
  points: string
}

export type MeanReversionBandGeometry = {
  id: string
  label: string
  polygon: string
}

export type MeanReversionLevelGeometry = {
  id: string
  role: AnalysisChartProjection['role']
  label: string
  price: number
  y: number
  width: number
}

export type MeanReversionGeometry = {
  lines: MeanReversionSeriesGeometry[]
  bands: MeanReversionBandGeometry[]
  levels: MeanReversionLevelGeometry[]
}

type PriceProjector = { priceToCoordinate: (price: number) => number | null }

export function projectMeanReversion(
  projections: AnalysisChartProjection[] | undefined,
  chart: IChartApi | null,
  priceSeries: PriceProjector | null,
  host: HTMLDivElement | null,
): MeanReversionGeometry | undefined {
  if (!projections?.length || !chart || !priceSeries || !host) return undefined
  const lines: MeanReversionSeriesGeometry[] = []
  const bands: MeanReversionBandGeometry[] = []
  const levels: MeanReversionLevelGeometry[] = []
  const timeScale = chart.timeScale()
  const coordinates = (points: Array<{ date: string; price: number }> | undefined) => (
    (points ?? []).flatMap(point => {
      if (!Number.isFinite(point.price)) return []
      const x = timeScale.timeToCoordinate(point.date as Time)
      const y = priceSeries.priceToCoordinate(point.price)
      return x === null || y === null || !Number.isFinite(x) || !Number.isFinite(y) ? [] : [{ x, y }]
    })
  )
  projections.forEach(projection => {
    if (projection.kind === 'series-line') {
      const values = coordinates(projection.points)
      if (values.length > 1) lines.push({
        id: projection.projection_id,
        role: projection.role,
        label: projection.label,
        points: values.map(point => `${point.x},${point.y}`).join(' '),
      })
      return
    }
    if (projection.kind === 'series-band') {
      const upper = coordinates(projection.upper_points)
      const lower = coordinates(projection.lower_points).reverse()
      if (upper.length > 1 && lower.length > 1) bands.push({
        id: projection.projection_id,
        label: projection.label,
        polygon: [...upper, ...lower].map(point => `${point.x},${point.y}`).join(' '),
      })
      return
    }
    if (typeof projection.price !== 'number' || !Number.isFinite(projection.price)) return
    const y = priceSeries.priceToCoordinate(projection.price)
    if (y !== null && Number.isFinite(y)) levels.push({
      id: projection.projection_id,
      role: projection.role,
      label: projection.label,
      price: projection.price,
      y,
      width: host.clientWidth,
    })
  })
  return { lines, bands, levels }
}
