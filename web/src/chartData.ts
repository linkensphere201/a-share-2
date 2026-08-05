import type { LineData, Time } from 'lightweight-charts'

export type DailyBar = {
  trade_date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  source: string
  bar_state?: 'final' | 'intraday'
  stale?: boolean
  provider_time?: string
}

export type Readout = DailyBar & { changePercent?: number; ma5?: number; ma20?: number; ma60?: number }
export type RenderBar = DailyBar & { period_start: string }

export type RangeMeasurement = {
  from: string
  to: string
  startAnchor: string
  endAnchor: string
  open: number
  close: number
  changePercent: number
  elapsedDays: number
  kLineCount: number
}

export type PriceGap = {
  direction: 'up' | 'down'
  previousDate: string
  startDate: string
  fillDate?: string
  lower: number
  upper: number
}

export type MacdPoint = {
  time: string
  dif: number
  dea: number
  histogram: number
}

const rising = '#ef5350'
const falling = '#26a269'
const risingSoft = '#e99693'
const fallingSoft = '#70be9a'

export function movingAverage(bars: DailyBar[], window: number): LineData<Time>[] {
  if (bars.length < window) return []
  const output: LineData<Time>[] = []
  let sum = 0
  for (let index = 0; index < bars.length; index += 1) {
    sum += bars[index].close
    if (index >= window) sum -= bars[index - window].close
    if (index >= window - 1) output.push({ time: bars[index].trade_date, value: sum / window })
  }
  return output
}

export function calculateMacd(
  bars: Pick<DailyBar, 'trade_date' | 'close'>[],
  fastPeriod = 12,
  slowPeriod = 26,
  signalPeriod = 9,
): MacdPoint[] {
  if (bars.length === 0) return []
  const fastAlpha = 2 / (fastPeriod + 1)
  const slowAlpha = 2 / (slowPeriod + 1)
  const signalAlpha = 2 / (signalPeriod + 1)
  let fast = bars[0].close
  let slow = bars[0].close
  let signal = 0
  return bars.map((bar, index) => {
    if (index > 0) {
      fast += fastAlpha * (bar.close - fast)
      slow += slowAlpha * (bar.close - slow)
    }
    const dif = fast - slow
    signal = index === 0 ? dif : signal + signalAlpha * (dif - signal)
    return { time: bar.trade_date, dif, dea: signal, histogram: 2 * (dif - signal) }
  })
}

export function mergeProvisionalBar(bars: DailyBar[], provisional: DailyBar): DailyBar[] {
  const last = bars.at(-1)
  if (!last) return [provisional]
  if (provisional.trade_date < last.trade_date) return bars
  if (provisional.trade_date === last.trade_date) {
    if (last.bar_state === 'final') return bars
    if (
      last.open === provisional.open
      && last.high === provisional.high
      && last.low === provisional.low
      && last.close === provisional.close
      && last.volume === provisional.volume
      && last.stale === provisional.stale
    ) return bars
    return [...bars.slice(0, -1), provisional]
  }
  return [...bars, provisional]
}

export function millisecondsUntilMarketSession(now: Date): number {
  const day = now.getDay()
  const minutes = now.getHours() * 60 + now.getMinutes()
  if (day >= 1 && day <= 5) {
    if (minutes < 9 * 60 + 30) return millisecondsUntil(now, 9, 30)
    if (minutes <= 11 * 60 + 30) return 0
    if (minutes < 13 * 60) return millisecondsUntil(now, 13, 0)
    if (minutes <= 15 * 60) return 0
  }
  return millisecondsUntilNextMarketDay(now)
}

export function millisecondsUntilNextMarketDay(now: Date): number {
  const target = new Date(now)
  target.setDate(target.getDate() + 1)
  target.setHours(9, 30, 0, 0)
  while (target.getDay() === 0 || target.getDay() === 6) target.setDate(target.getDate() + 1)
  return Math.max(1000, target.getTime() - now.getTime())
}

function millisecondsUntil(now: Date, hour: number, minute: number): number {
  const target = new Date(now)
  target.setHours(hour, minute, 0, 0)
  return Math.max(1000, target.getTime() - now.getTime())
}

export function aggregateBars(bars: DailyBar[], bucket: number): RenderBar[] {
  if (bucket <= 1) return bars.map(item => ({ ...item, period_start: item.trade_date }))
  const output: RenderBar[] = []
  for (let start = 0; start < bars.length; start += bucket) {
    const group = bars.slice(start, start + bucket)
    const first = group[0]
    const last = group.at(-1)!
    output.push({
      trade_date: last.trade_date,
      period_start: first.trade_date,
      open: first.open,
      high: Math.max(...group.map(item => item.high)),
      low: Math.min(...group.map(item => item.low)),
      close: last.close,
      volume: group.reduce((sum, item) => sum + item.volume, 0),
      source: first.source === last.source ? first.source : 'mixed',
    })
  }
  return output
}

export function chooseLodBucket(visibleBars: number, width: number): number {
  if (visibleBars <= 0 || width <= 0) return 1
  const required = visibleBars / Math.max(1, width * 1.25)
  if (required <= 1) return 1
  return Math.min(32, 2 ** Math.ceil(Math.log2(required)))
}

export function calculatePriceScaleMargins(
  visibleBars: number,
  width: number,
  low?: number,
  high?: number,
): { top: number; bottom: number } {
  const density = Math.max(0, visibleBars) / Math.max(1, width)
  const compression = Math.max(1, density / 0.35)
  const occupancy = clamp(0.88 / compression ** 0.25, 0.38, 0.88)
  const totalMargin = 1 - occupancy
  let bottom = totalMargin / 2
  if (low !== undefined && high !== undefined && low > 0 && high > low) {
    bottom = Math.min(bottom, low * occupancy / (high - low))
  }
  return { top: totalMargin - bottom, bottom }
}

export function calculateChangePercent(close: number, previousClose?: number): number | undefined {
  if (previousClose === undefined || previousClose === 0) return undefined
  return (close - previousClose) / previousClose * 100
}

export function candleColor(bar: Pick<DailyBar, 'open' | 'close'>, previousClose?: number): string {
  const reference = previousClose ?? bar.open
  const changePercent = calculateChangePercent(bar.close, reference) ?? 0
  if (changePercent >= 0) return changePercent < 3 ? risingSoft : rising
  return Math.abs(changePercent) < 3 ? fallingSoft : falling
}

export function visibleExtrema(
  bars: DailyBar[],
  from: string,
  to: string,
): { high: DailyBar; low: DailyBar } | undefined {
  let high: DailyBar | undefined
  let low: DailyBar | undefined
  for (const bar of bars) {
    if (bar.trade_date < from || bar.trade_date > to) continue
    if (!high || bar.high > high.high) high = bar
    if (!low || bar.low < low.low) low = bar
  }
  return high && low ? { high, low } : undefined
}

export function detectPriceGaps(bars: DailyBar[]): PriceGap[] {
  const gaps: PriceGap[] = []
  for (let index = 1; index < bars.length; index += 1) {
    const previous = bars[index - 1]
    const current = bars[index]
    let gap: PriceGap | undefined
    if (current.low > previous.high) {
      gap = { direction: 'up', previousDate: previous.trade_date, startDate: current.trade_date, lower: previous.high, upper: current.low }
    } else if (current.high < previous.low) {
      gap = { direction: 'down', previousDate: previous.trade_date, startDate: current.trade_date, lower: current.high, upper: previous.low }
    }
    if (!gap) continue
    for (let fillIndex = index + 1; fillIndex < bars.length; fillIndex += 1) {
      const candidate = bars[fillIndex]
      const filled = gap.direction === 'up' ? candidate.low <= gap.lower : candidate.high >= gap.upper
      if (filled) {
        gap.fillDate = candidate.trade_date
        break
      }
    }
    gaps.push(gap)
  }
  return gaps
}

export function visibleUnfilledPriceGaps(gaps: PriceGap[], to: string, limit = 4): PriceGap[] {
  return gaps.filter(gap => gap.fillDate === undefined && gap.startDate <= to).slice(-Math.max(0, limit))
}

export function createRangeMeasurement(
  first: DailyBar & { period_start?: string },
  last: DailyBar,
  kLineCount: number,
): RangeMeasurement {
  const from = first.period_start ?? first.trade_date
  return {
    from,
    to: last.trade_date,
    startAnchor: first.trade_date,
    endAnchor: last.trade_date,
    open: first.open,
    close: last.close,
    changePercent: calculateChangePercent(last.close, first.open) ?? 0,
    elapsedDays: calendarDaysBetween(from, last.trade_date),
    kLineCount,
  }
}

export function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value))
}

export function previousCloseByDate(bars: DailyBar[]): Map<string, number> {
  const output = new Map<string, number>()
  for (let index = 1; index < bars.length; index += 1) output.set(bars[index].trade_date, bars[index - 1].close)
  return output
}

export function visibleBarStats(
  bars: DailyBar[],
  from?: string,
  to?: string,
): { count: number; low?: number; high?: number } {
  let count = 0
  let low = Number.POSITIVE_INFINITY
  let high = Number.NEGATIVE_INFINITY
  for (const bar of bars) {
    if ((from && bar.trade_date < from) || (to && bar.trade_date > to)) continue
    count += 1
    low = Math.min(low, bar.low)
    high = Math.max(high, bar.high)
  }
  return { count, low: Number.isFinite(low) ? low : undefined, high: Number.isFinite(high) ? high : undefined }
}

export function latestReadout(bars: DailyBar[]): Readout | null {
  const latest = bars.at(-1)
  if (!latest) return null
  return {
    ...latest,
    changePercent: calculateChangePercent(latest.close, bars.at(-2)?.close),
    ma5: latestAverage(bars, 5),
    ma20: latestAverage(bars, 20),
    ma60: latestAverage(bars, 60),
  }
}

function latestAverage(bars: DailyBar[], window: number): number | undefined {
  if (bars.length < window) return undefined
  return bars.slice(-window).reduce((sum, item) => sum + item.close, 0) / window
}

export function subtractYears(date: string, years: number): string {
  const value = new Date(`${date}T00:00:00Z`)
  value.setUTCFullYear(value.getUTCFullYear() - years)
  return value.toISOString().slice(0, 10)
}

export function subtractMonths(date: string, months: number): string {
  const value = new Date(`${date}T00:00:00Z`)
  value.setUTCMonth(value.getUTCMonth() - months)
  return value.toISOString().slice(0, 10)
}

function calendarDaysBetween(from: string, to: string): number {
  const start = Date.parse(`${from}T00:00:00Z`)
  const end = Date.parse(`${to}T00:00:00Z`)
  return Math.max(0, Math.round((end - start) / 86_400_000))
}
