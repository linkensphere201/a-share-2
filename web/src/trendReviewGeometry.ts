import type { TrendAnalysisRun } from './trendAnalysisClient'
import type { TrendReviewLabel } from './trendReviewClient'

export type TrendReviewGeometryHandle = {
  id: string
  label: string
  date?: string
  price: number
  priceOnly?: boolean
}

export type TrendReviewGeometryPoint = { date?: string; price: number }

export type TrendReviewGeometryTarget = {
  label: TrendReviewLabel
  onChange: (label: TrendReviewLabel) => void
}

export function reviewGeometryHandles(label?: TrendReviewLabel): TrendReviewGeometryHandle[] {
  if (!label) return []
  const payload = label.payload
  if (label.item_type === 'anchor') {
    return handle('anchor', '拐点', payload.pivot_date, payload.price)
  }
  if (label.item_type === 'line') {
    return [
      ...handle('line:first', '趋势线起点', payload.first_pivot_date, payload.first_price),
      ...handle('line:second', '趋势线终点', payload.second_pivot_date, payload.second_price),
    ]
  }
  if (label.item_type === 'zone') {
    return [
      ...priceHandle('zone:lower', '区域下界', payload.lower),
      ...priceHandle('zone:upper', '区域上界', payload.upper),
    ]
  }
  if (label.item_type === 'pattern') return patternHandles(payload)
  if (label.item_type === 'transition') {
    const date = stringValue(payload.event_date) ?? stringValue(payload.transition_date)
    const evidence = objectValue(payload.evidence)
    const price = numberValue(payload.boundary_price) ?? numberValue(evidence?.close)
    return date && price !== undefined
      ? [{ id: 'transition', label: '事件位置', date, price }]
      : []
  }
  return []
}

export function updateReviewGeometryHandle(
  label: TrendReviewLabel,
  handleId: string,
  point: TrendReviewGeometryPoint,
): TrendReviewLabel {
  const payload = structuredClone(label.payload)
  if (handleId === 'anchor') {
    assignDatePrice(payload, 'pivot_date', 'price', point)
  } else if (handleId === 'line:first') {
    assignDatePrice(payload, 'first_pivot_date', 'first_price', point)
  } else if (handleId === 'line:second') {
    assignDatePrice(payload, 'second_pivot_date', 'second_price', point)
  } else if (handleId === 'zone:lower') {
    payload.lower = Math.min(point.price, numberValue(payload.upper) ?? point.price)
  } else if (handleId === 'zone:upper') {
    payload.upper = Math.max(point.price, numberValue(payload.lower) ?? point.price)
  } else if (handleId === 'pattern:neckline') {
    payload.neckline_price = point.price
  } else if (handleId.startsWith('pattern:pivot:')) {
    const index = Number(handleId.split(':')[2])
    const pivots = Array.isArray(payload.pivots) ? structuredClone(payload.pivots) : []
    if (Number.isInteger(index) && objectValue(pivots[index])) {
      assignDatePrice(pivots[index] as Record<string, unknown>, 'pivot_date', 'price', point)
      payload.pivots = pivots
      updatePatternInterval(payload)
    }
  } else if (handleId.startsWith('pattern:boundary:')) {
    updatePatternBoundary(payload, handleId, point)
  } else if (handleId === 'transition') {
    const dateKey = typeof payload.event_date === 'string' ? 'event_date' : 'transition_date'
    if (point.date) payload[dateKey] = point.date
    if (typeof payload.boundary_price === 'number') payload.boundary_price = point.price
    const evidence = objectValue(payload.evidence)
    if (evidence && typeof evidence.close === 'number') {
      payload.evidence = { ...evidence, close: point.price, ...(point.date ? { trade_date: point.date } : {}) }
    }
  }
  return { ...label, payload }
}

export function mergeReviewLabelsIntoAnalysis(
  analysis: TrendAnalysisRun,
  labels: TrendReviewLabel[],
): TrendAnalysisRun {
  const byId = new Map(labels.map(label => [label.item_id, label]))
  const existing = new Set(analysis.items.map(item => item.item_id))
  return {
    ...analysis,
    items: [
      ...analysis.items.map(item => {
        const label = byId.get(item.item_id)
        return label ? { ...item, item_type: label.item_type as typeof item.item_type, payload: label.payload } : item
      }),
      ...labels.filter(label => !existing.has(label.item_id)).map(label => ({
        item_id: label.item_id,
        item_type: label.item_type as typeof analysis.items[number]['item_type'],
        payload: label.payload,
      })),
    ],
  }
}

function patternHandles(payload: Record<string, unknown>): TrendReviewGeometryHandle[] {
  const handles: TrendReviewGeometryHandle[] = []
  if (Array.isArray(payload.pivots)) {
    payload.pivots.forEach((value, index) => {
      const pivot = objectValue(value)
      if (!pivot) return
      handles.push(...handle(`pattern:pivot:${index}`, `形态拐点 ${index + 1}`, pivot.pivot_date, pivot.price))
    })
  }
  handles.push(...priceHandle('pattern:neckline', '颈线', payload.neckline_price))
  const geometry = objectValue(payload.boundary_geometry)
  if (!geometry) return handles
  for (const key of ['upper', 'lower']) {
    handles.push(...boundaryHandles(objectValue(geometry[key]), `pattern:boundary:${key}`, `${key === 'upper' ? '上' : '下'}边界`))
  }
  if (Array.isArray(geometry.segments)) {
    geometry.segments.forEach((value, index) => {
      handles.push(...boundaryHandles(objectValue(value), `pattern:boundary:segment:${index}`, `边界 ${index + 1}`))
    })
  }
  return handles
}

function boundaryHandles(
  boundary: Record<string, unknown> | undefined,
  id: string,
  label: string,
): TrendReviewGeometryHandle[] {
  if (!boundary) return []
  return [
    ...handle(`${id}:start`, `${label}起点`, boundary.start_date, boundary.start_price),
    ...handle(`${id}:end`, `${label}终点`, boundary.end_date, boundary.end_price),
  ]
}

function updatePatternBoundary(
  payload: Record<string, unknown>,
  handleId: string,
  point: TrendReviewGeometryPoint,
): void {
  const parts = handleId.split(':')
  const endpoint = parts.at(-1)
  const geometry = objectValue(payload.boundary_geometry)
  if (!geometry || (endpoint !== 'start' && endpoint !== 'end')) return
  if (parts[2] === 'segment') {
    const index = Number(parts[3])
    const segments = Array.isArray(geometry.segments) ? structuredClone(geometry.segments) : []
    const boundary = objectValue(segments[index])
    if (!boundary) return
    assignDatePrice(boundary, `${endpoint}_date`, `${endpoint}_price`, point)
    segments[index] = boundary
    payload.boundary_geometry = { ...geometry, segments }
    return
  }
  const boundary = objectValue(geometry[parts[2]])
  if (!boundary) return
  assignDatePrice(boundary, `${endpoint}_date`, `${endpoint}_price`, point)
  payload.boundary_geometry = { ...geometry, [parts[2]]: boundary }
}

function updatePatternInterval(payload: Record<string, unknown>): void {
  if (!Array.isArray(payload.pivots)) return
  const dates = payload.pivots.flatMap(value => {
    const date = stringValue(objectValue(value)?.pivot_date)
    return date ? [date] : []
  }).sort()
  if (dates.length > 0) {
    payload.start_date = dates[0]
    payload.end_date = dates.at(-1)
  }
}

function handle(
  id: string,
  label: string,
  dateValue: unknown,
  priceValue: unknown,
): TrendReviewGeometryHandle[] {
  const date = stringValue(dateValue)
  const price = numberValue(priceValue)
  return date && price !== undefined ? [{ id, label, date, price }] : []
}

function priceHandle(id: string, label: string, value: unknown): TrendReviewGeometryHandle[] {
  const price = numberValue(value)
  return price === undefined ? [] : [{ id, label, price, priceOnly: true }]
}

function assignDatePrice(
  target: Record<string, unknown>,
  dateKey: string,
  priceKey: string,
  point: TrendReviewGeometryPoint,
): void {
  if (point.date) target[dateKey] = point.date
  target[priceKey] = point.price
}

function objectValue(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined
}

function numberValue(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}
