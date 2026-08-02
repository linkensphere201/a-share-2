import { describe, expect, it } from 'vitest'

import { movingAverage, type DailyBar } from './ChartCanvas'

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
