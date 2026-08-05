import type { DailyBar } from './chartData'
import type { TrendLineAnchor, TrendLineSnap } from './drawingStore'

export type RenderPeriod = {
  period_start: string
  trade_date: string
}

export type AnchorCandidate = {
  date: string
  price: number
  snap: Exclude<TrendLineSnap, 'free'>
  x: number
  y: number
}

export type LineGeometry = {
  x1: number
  y1: number
  x2: number
  y2: number
}

export function chooseAnchor(
  x: number,
  y: number,
  fallback: TrendLineAnchor,
  candidates: AnchorCandidate[],
  xTolerance = 9,
  yTolerance = 12,
): TrendLineAnchor {
  let best: AnchorCandidate | undefined
  let bestDistance = Number.POSITIVE_INFINITY
  for (const candidate of candidates) {
    const dx = Math.abs(candidate.x - x)
    const dy = Math.abs(candidate.y - y)
    if (dx > xTolerance || dy > yTolerance) continue
    const distance = Math.hypot(dx, dy)
    if (distance < bestDistance) {
      best = candidate
      bestDistance = distance
    }
  }
  return best ? { date: best.date, price: best.price, snap: best.snap } : fallback
}

export function barsInRenderPeriod(period: RenderPeriod, bars: DailyBar[]): DailyBar[] {
  return bars.filter(bar => bar.trade_date >= period.period_start && bar.trade_date <= period.trade_date)
}

export function renderDateForAnchor(anchorDate: string, periods: RenderPeriod[]): string | undefined {
  const containing = periods.find(period => anchorDate >= period.period_start && anchorDate <= period.trade_date)
  if (containing) return containing.trade_date
  if (periods.length === 0) return undefined
  if (anchorDate < periods[0].period_start) return periods[0].trade_date
  if (anchorDate > periods.at(-1)!.trade_date) return periods.at(-1)!.trade_date
  return periods.reduce((nearest, period) => (
    dateDistance(anchorDate, period.trade_date) < dateDistance(anchorDate, nearest.trade_date) ? period : nearest
  )).trade_date
}

export function extendLineToBounds(line: LineGeometry, width: number, height: number): LineGeometry {
  const dx = line.x2 - line.x1
  const dy = line.y2 - line.y1
  if ((Math.abs(dx) < 0.001 && Math.abs(dy) < 0.001) || width <= 0 || height <= 0) return line
  const points: Array<{ t: number; x: number; y: number }> = []
  const add = (t: number, x: number, y: number) => {
    if (x < -0.01 || x > width + 0.01 || y < -0.01 || y > height + 0.01) return
    if (points.some(point => Math.abs(point.x - x) < 0.01 && Math.abs(point.y - y) < 0.01)) return
    points.push({ t, x: clamp(x, 0, width), y: clamp(y, 0, height) })
  }
  if (Math.abs(dx) >= 0.001) {
    const leftT = -line.x1 / dx
    const rightT = (width - line.x1) / dx
    add(leftT, 0, line.y1 + leftT * dy)
    add(rightT, width, line.y1 + rightT * dy)
  }
  if (Math.abs(dy) >= 0.001) {
    const topT = -line.y1 / dy
    const bottomT = (height - line.y1) / dy
    add(topT, line.x1 + topT * dx, 0)
    add(bottomT, line.x1 + bottomT * dx, height)
  }
  if (points.length < 2) return line
  points.sort((left, right) => left.t - right.t)
  const first = points[0]
  const last = points.at(-1)!
  return { x1: first.x, y1: first.y, x2: last.x, y2: last.y }
}

function dateDistance(left: string, right: string): number {
  return Math.abs(Date.parse(`${left}T00:00:00Z`) - Date.parse(`${right}T00:00:00Z`))
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value))
}
