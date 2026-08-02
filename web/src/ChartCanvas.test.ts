import { describe, expect, it } from 'vitest'

import { aggregateBars, chooseLodBucket, movingAverage, type DailyBar } from './ChartCanvas'

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
