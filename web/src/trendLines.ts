import type { DailyBar } from './ChartCanvas'
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

function dateDistance(left: string, right: string): number {
  return Math.abs(Date.parse(`${left}T00:00:00Z`) - Date.parse(`${right}T00:00:00Z`))
}

