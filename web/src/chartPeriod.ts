import type { DailyBar } from './chartData'

export type ChartPeriod = 'daily' | 'monthly'

// Input is the ordered, cutoff-bounded daily history used by the chart.
export function monthlyBars(daily: DailyBar[]): DailyBar[] {
  const result: DailyBar[] = []
  for (const bar of daily) {
    const month = result.at(-1)
    if (!month || month.trade_date.slice(0, 7) !== bar.trade_date.slice(0, 7)) {
      result.push({ ...bar, period_start: bar.trade_date, turnover_rate_f: undefined })
      continue
    }
    result[result.length - 1] = {
      ...bar,
      period_start: month.period_start,
      open: month.open,
      high: Math.max(month.high, bar.high),
      low: Math.min(month.low, bar.low),
      volume: month.volume + bar.volume,
      amount: month.amount == null || bar.amount == null ? null : month.amount + bar.amount,
      previous_close: month.previous_close,
      previous_settlement: month.previous_settlement,
      open_interest_change: month.open_interest_change == null || bar.open_interest_change == null
        ? null : month.open_interest_change + bar.open_interest_change,
      turnover_rate_f: undefined,
      roll_event: Boolean(month.roll_event || bar.roll_event),
      source: month.source === bar.source ? bar.source : 'mixed',
      stale: Boolean(month.stale || bar.stale),
    }
  }
  return result
}
