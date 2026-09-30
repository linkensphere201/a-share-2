import { describe, expect, it } from 'vitest'
import { monthlyBars } from './chartPeriod'
import { aggregateBars, createRangeMeasurement, latestReadout, previousCloseByDate, type DailyBar } from './chartData'

const bar = (date: string, close: number, extra: Partial<DailyBar> = {}): DailyBar => ({
  trade_date: date, open: close - 1, high: close + 2, low: close - 2,
  close, volume: 100, amount: 200, turnover_rate_f: 2, source: 'test', ...extra,
})

describe('calendar monthly chart bars', () => {
  it('aggregates calendar boundaries, OHLC and additive fields without mutating daily bars', () => {
    const daily = [bar('2025-12-31', 8), bar('2026-01-05', 10), bar('2026-01-30', 12), bar('2026-02-02', 11)]
    const before = structuredClone(daily)
    const months = monthlyBars(daily)
    expect(months).toHaveLength(3)
    expect(months[1]).toMatchObject({ trade_date: '2026-01-30', period_start: '2026-01-05', open: 9,
      high: 14, low: 8, close: 12, volume: 200, amount: 400 })
    expect(months.every(item => item.turnover_rate_f === undefined)).toBe(true)
    expect(daily).toEqual(before)
    expect(previousCloseByDate(months).get('2026-01-05')).toBe(8)
    expect(latestReadout(months)?.changePercent).toBeCloseTo((11 / 12 - 1) * 100)
  })

  it('retains partial/cutoff months and provisional latest values without inventing future dates', () => {
    const daily = [bar('2026-01-05', 10), bar('2026-01-12', 11, { bar_state: 'intraday', stale: true })]
    expect(monthlyBars(daily)[0]).toMatchObject({ trade_date: '2026-01-12', volume: 200, bar_state: 'intraday', stale: true })
    expect(monthlyBars(daily.slice(0, 1))[0].close).toBe(10)
    expect(monthlyBars([])).toEqual([])
  })

  it('keeps stock turnover measurement on original daily data and month start', () => {
    const daily = [bar('2026-01-05', 10), bar('2026-01-30', 12), bar('2026-02-02', 11)]
    const rendered = aggregateBars(monthlyBars(daily), 1)
    expect(rendered[0].period_start).toBe('2026-01-05')
    const measurement = createRangeMeasurement(rendered[0], rendered[1], 2, 0, daily)
    expect(measurement.turnover).toMatchObject({ sum: 6, total: 3, available: 3 })
  })

  it('uses monthly closes for moving averages and preserves futures stock/flow semantics', () => {
    const months = monthlyBars(Array.from({ length: 5 }, (_, i) => bar(`2026-0${i + 1}-15`, 10 + i)))
    expect(latestReadout(months)?.ma5).toBe(12)
    const futures = monthlyBars([
      bar('2026-01-05', 10, { previous_settlement: 8, open_interest: 20, open_interest_change: 2, roll_event: true }),
      bar('2026-01-30', 12, { previous_settlement: 11, open_interest: 25, open_interest_change: 5, amount: null }),
    ])[0]
    expect(futures).toMatchObject({ previous_settlement: 8, open_interest: 25, open_interest_change: 7, roll_event: true, amount: null })
  })
})
