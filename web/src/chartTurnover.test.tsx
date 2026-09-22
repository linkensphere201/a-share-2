import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { ChartReadout } from './chartCanvasParts'
import { aggregateBars, latestReadout, type DailyBar } from './chartData'

const bar: DailyBar = {
  trade_date: '2026-09-16', open: 10, high: 12, low: 9, close: 11,
  volume: 100, source: 'tushare', turnover_rate_f: 2.35,
}

describe('daily turnover readout', () => {
  it('preserves date-specific percentages for daily and latest readouts', () => {
    const bars = [bar, { ...bar, trade_date: '2026-09-17', turnover_rate_f: 0 }]
    expect(aggregateBars(bars, 1).map(item => item.turnover_rate_f)).toEqual([2.35, 0])
    expect(latestReadout(bars)?.turnover_rate_f).toBe(0)
    const html = renderToStaticMarkup(<ChartReadout value={bar} futures={false}/> )
    expect(html).toContain('2.35%')
    expect(html).toContain('自由流通')
  })

  it('distinguishes missing data from real zero and omits unsupported series', () => {
    for (const [rate, expected] of [[null, '--'], [0, '0.00%']] as const) {
      const html = renderToStaticMarkup(<ChartReadout value={{ ...bar, turnover_rate_f: rate }} futures={false}/> )
      expect(html).toContain(`<b>${expected}</b>`)
    }
    expect(renderToStaticMarkup(<ChartReadout value={{ ...bar, turnover_rate_f: undefined }} futures={false}/>)).not.toContain('自由流通')
    expect(renderToStaticMarkup(<ChartReadout value={bar} futures={true}/>)).not.toContain('自由流通')
  })

  it('does not carry a daily percentage onto a multi-day aggregate', () => {
    const bars = [bar, { ...bar, trade_date: '2026-09-17', turnover_rate_f: 4.2 },
      { ...bar, trade_date: '2026-09-18', turnover_rate_f: null }]
    const groups = aggregateBars(bars, 2)
    expect(groups[0].turnover_rate_f).toBeUndefined()
    expect(groups[1].turnover_rate_f).toBeNull()
    expect(latestReadout(bars)?.turnover_rate_f).toBeNull()
  })
})
