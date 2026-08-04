import { describe, expect, it } from 'vitest'

import {
  aggregateBars,
  calculateChangePercent,
  candleColor,
  chooseLodBucket,
  createRangeMeasurement,
  movingAverage,
  mergeProvisionalBar,
  millisecondsUntilMarketSession,
  type DailyBar,
} from './ChartCanvas'

describe('movingAverage', () => {
  it('starts only after the full window and uses close prices', () => {
    const bars: DailyBar[] = [1, 2, 3, 4, 5].map((close, index) => ({
      trade_date: `2026-08-0${index + 1}`,
      open: close,
      high: close,
      low: close,
      close,
      volume: 100,
      source: 'test',
    }))

    expect(movingAverage(bars, 3)).toEqual([
      { time: '2026-08-03', value: 2 },
      { time: '2026-08-04', value: 3 },
      { time: '2026-08-05', value: 4 },
    ])
  })
})

describe('provisional daily bars', () => {
  const finalBar: DailyBar = {
    trade_date: '2026-08-03', open: 10, high: 11, low: 9, close: 10, volume: 100,
    source: 'tushare', bar_state: 'final',
  }
  const liveBar: DailyBar = {
    trade_date: '2026-08-04', open: 10, high: 12, low: 9, close: 11, volume: 200,
    source: 'eastmoney_selected', bar_state: 'intraday', stale: false,
  }

  it('appends and then replaces only the current provisional day', () => {
    const appended = mergeProvisionalBar([finalBar], liveBar)
    expect(appended).toHaveLength(2)
    expect(mergeProvisionalBar(appended, { ...liveBar, close: 11.5 }).at(-1)?.close).toBe(11.5)
    expect(mergeProvisionalBar([finalBar], { ...liveBar, trade_date: finalBar.trade_date })).toEqual([finalBar])
  })

  it('does not poll during lunch or after close', () => {
    expect(millisecondsUntilMarketSession(new Date(2026, 7, 4, 10, 0))).toBe(0)
    expect(millisecondsUntilMarketSession(new Date(2026, 7, 4, 12, 0))).toBeGreaterThan(0)
    expect(millisecondsUntilMarketSession(new Date(2026, 7, 4, 15, 1))).toBeGreaterThan(0)
  })
})

describe('chart level of detail', () => {
  const bars: DailyBar[] = [
    { trade_date: '2026-08-01', open: 10, high: 12, low: 9, close: 11, volume: 100, source: 'a' },
    { trade_date: '2026-08-02', open: 11, high: 14, low: 10, close: 13, volume: 200, source: 'a' },
    { trade_date: '2026-08-03', open: 13, high: 13, low: 8, close: 9, volume: 300, source: 'b' },
  ]

  it('preserves OHLC extrema and sums volume', () => {
    expect(aggregateBars(bars, 2)).toEqual([
      {
        trade_date: '2026-08-02', period_start: '2026-08-01',
        open: 10, high: 14, low: 9, close: 13, volume: 300, source: 'a',
      },
      {
        trade_date: '2026-08-03', period_start: '2026-08-03',
        open: 13, high: 13, low: 8, close: 9, volume: 300, source: 'b',
      },
    ])
  })

  it('uses power-of-two buckets only when density exceeds the viewport', () => {
    expect(chooseLodBucket(800, 800)).toBe(1)
    expect(chooseLodBucket(5_457, 800)).toBe(8)
  })
})

describe('daily change percentage', () => {
  it('calculates rise and fall against the previous close', () => {
    expect(calculateChangePercent(11, 10)).toBeCloseTo(10)
    expect(calculateChangePercent(9, 10)).toBeCloseTo(-10)
  })

  it('returns no value when the previous close is unavailable or zero', () => {
    expect(calculateChangePercent(10)).toBeUndefined()
    expect(calculateChangePercent(10, 0)).toBeUndefined()
  })
})

describe('candlestick change colors', () => {
  const bar = (open: number, close: number) => ({ open, close })

  it('uses pale red and green below the three-percent threshold', () => {
    expect(candleColor(bar(10, 10.2), 10)).toBe('#e99693')
    expect(candleColor(bar(10, 9.8), 10)).toBe('#70be9a')
  })

  it('uses strong red and green at or beyond three percent', () => {
    expect(candleColor(bar(10, 10.3), 10)).toBe('#ef5350')
    expect(candleColor(bar(10, 9.7), 10)).toBe('#26a269')
  })

  it('falls back to the open when no previous close exists', () => {
    expect(candleColor(bar(10, 10.1))).toBe('#e99693')
  })
})

describe('selected range measurement', () => {
  it('measures from the first open to the last close', () => {
    const first: DailyBar = {
      trade_date: '2026-07-01', open: 10, high: 11, low: 9, close: 10.5, volume: 100, source: 'test',
    }
    const last: DailyBar = {
      trade_date: '2026-07-31', open: 11, high: 13, low: 10, close: 12, volume: 200, source: 'test',
    }

    expect(createRangeMeasurement(first, last, 23)).toEqual({
      from: '2026-07-01',
      to: '2026-07-31',
      startAnchor: '2026-07-01',
      endAnchor: '2026-07-31',
      open: 10,
      close: 12,
      changePercent: 20,
      elapsedDays: 30,
      kLineCount: 23,
    })
  })
})
