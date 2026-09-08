import { describe, expect, it } from 'vitest'

import {
  chartHandleScaleOptions,
  chartLayoutOptions,
  compactCrosshairMarkerOptions,
  dailyBarsUrl,
  paneInteractionOptions,
} from './ChartCanvas'
import {
  aggregateBars,
  calculateMacd,
  calculateChangePercent,
  candleColor,
  chooseLodBucket,
  remapLogicalRange,
  snapLogicalRangeToDataEdge,
  createRangeMeasurement,
  detectPriceGaps,
  latestReadout,
  middleMovingAveragePeriod,
  movingAverage,
  mergeProvisionalBar,
  millisecondsUntilMarketSession,
  priceRangeForChartScale,
  shouldUseFinalDailyRefresh,
  visibleExtrema,
  visibleUnfilledPriceGaps,
  type DailyBar,
  type PriceGap,
} from './chartData'

describe('chart layout', () => {
  it('does not render the TradingView attribution over the chart', () => {
    expect(chartLayoutOptions.attributionLogo).toBe(false)
  })

  it('uses MA13 only for the active-market-value index', () => {
    expect(middleMovingAveragePeriod('SHAMV.A')).toBe(13)
    expect(middleMovingAveragePeriod('000001.SZ')).toBe(20)
  })

  it('physically bounds review-mode bar requests at the causal cutoff', () => {
    expect(dailyBarsUrl('000001.SZ', '2026-08-18')).toBe(
      '/api/instruments/000001.SZ/daily-bars?end_date=2026-08-18',
    )
    expect(dailyBarsUrl('000001.SZ')).toBe('/api/instruments/000001.SZ/daily-bars')
    expect(dailyBarsUrl('FUTCONT:SHFE:CU:MAIN:raw')).toBe(
      '/api/instruments/FUTCONT%3ASHFE%3ACU%3AMAIN%3Araw/daily-bars',
    )
  })

  it('uses compact line-series markers at crosshair intersections', () => {
    expect(compactCrosshairMarkerOptions).toEqual({
      crosshairMarkerRadius: 2,
      crosshairMarkerBorderWidth: 1,
    })
  })

  it('disables pane separator hover and resize when the chart window is unfocused', () => {
    expect(paneInteractionOptions('#333', '#4b84c6', false)).toEqual({
      separatorColor: '#333', separatorHoverColor: '#333', enableResize: false,
    })
    expect(paneInteractionOptions('#333', '#4b84c6', true)).toEqual({
      separatorColor: '#333', separatorHoverColor: '#4b84c6', enableResize: true,
    })
  })

  it('leaves vertical price interaction to the bounded StockHarness viewport', () => {
    expect(chartHandleScaleOptions.axisPressedMouseMove).toEqual({ time: false, price: false })
    expect(chartHandleScaleOptions.mouseWheel).toBe(false)
  })
})

describe('bounded manual price viewport', () => {
  it('encodes raw prices for the Lightweight Charts logarithmic range API', () => {
    const encoded = priceRangeForChartScale({ from: 100, to: 1000 }, true)
    expect(encoded.from).toBeCloseTo(6.0000004)
    expect(encoded.to).toBeCloseTo(7.00000004)
    expect(priceRangeForChartScale({ from: 100, to: 1000 })).toEqual({ from: 100, to: 1000 })
  })

})

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

describe('MACD', () => {
  it('uses 2x DIF minus DEA histogram semantics', () => {
    const values = calculateMacd([
      { trade_date: '2026-08-01', close: 10 },
      { trade_date: '2026-08-02', close: 11 },
      { trade_date: '2026-08-03', close: 12 },
    ], 2, 3, 2)
    expect(values[0]).toEqual({ time: '2026-08-01', dif: 0, dea: 0, histogram: 0 })
    expect(values[1].dif).toBeCloseTo(1 / 6)
    expect(values[1].dea).toBeCloseTo(1 / 9)
    expect(values[1].histogram).toBeCloseTo(1 / 9)
    expect(values[2].histogram).toBeGreaterThan(0)
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

  it('routes before-open, after-close, and weekend refreshes to final daily updates', () => {
    expect(shouldUseFinalDailyRefresh(new Date(2026, 7, 4, 8, 30))).toBe(true)
    expect(shouldUseFinalDailyRefresh(new Date(2026, 7, 4, 10, 0))).toBe(false)
    expect(shouldUseFinalDailyRefresh(new Date(2026, 7, 4, 12, 0))).toBe(false)
    expect(shouldUseFinalDailyRefresh(new Date(2026, 7, 4, 15, 1))).toBe(true)
    expect(shouldUseFinalDailyRefresh(new Date(2026, 7, 8, 10, 0))).toBe(true)
  })

  it('uses previous settlement for futures readout change', () => {
    const readout = latestReadout([{
      ...finalBar,
      close: 103,
      previous_settlement: 100,
      settlement: 102,
      open_interest: 12_000,
      open_interest_change: 300,
      mapped_contract_symbol: 'FUT:SHFE:CU:202609',
    }])
    expect(readout?.changePercent).toBeCloseTo(3)
    expect(readout?.settlement).toBe(102)
    expect(readout?.mapped_contract_symbol).toBe('FUT:SHFE:CU:202609')
  })

  it('supports a configurable middle moving-average period', () => {
    const bars = Array.from({ length: 13 }, (_, index) => ({
      trade_date: `2026-08-${String(index + 1).padStart(2, '0')}`,
      open: index + 1,
      high: index + 2,
      low: index || 0.5,
      close: index + 1,
      volume: 0,
      source: 'stock_harness_amv',
    }))
    expect(latestReadout(bars, 13)?.ma20).toBe(7)
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

  it('retains ending futures settlement and open interest in LOD bars', () => {
    const result = aggregateBars([
      { ...bars[0], settlement: 11.2, open_interest: 1000 },
      { ...bars[1], settlement: 12.8, open_interest: 1200, open_interest_change: 200 },
    ], 2)[0]
    expect(result).toMatchObject({
      open: 10, high: 14, low: 9, close: 13, volume: 300,
      settlement: 12.8, open_interest: 1200, open_interest_change: 200,
    })
  })

  it('uses power-of-two buckets only when density exceeds the viewport', () => {
    expect(chooseLodBucket(800, 800)).toBe(1)
    expect(chooseLodBucket(5_457, 800)).toBe(8)
  })

  it('keeps off-data whitespace when LOD data counts change', () => {
    expect(remapLogicalRange({ from: -20, to: 120 }, 101, 51)).toEqual({ from: -10, to: 60 })
  })

  it('snaps only after a data edge is within the pixel magnet threshold', () => {
    expect(snapLogicalRangeToDataEdge({ from: -1, to: 99 }, 100, 10)).toEqual({ from: 0, to: 100 })
    expect(snapLogicalRangeToDataEdge({ from: -3, to: 97 }, 100, 10)).toBeUndefined()
    expect(snapLogicalRangeToDataEdge({ from: 2, to: 101 }, 100, 10)).toEqual({ from: 3, to: 102 })
  })
})

describe('market annotations', () => {
  const bar = (trade_date: string, open: number, high: number, low: number, close: number): DailyBar => ({
    trade_date, open, high, low, close, volume: 100, source: 'test',
  })

  it('finds the highest high and lowest low inside the visible date range', () => {
    const bars = [
      bar('2026-08-01', 10, 15, 8, 12),
      bar('2026-08-02', 12, 14, 9, 13),
      bar('2026-08-03', 13, 16, 11, 15),
    ]
    const extrema = visibleExtrema(bars, '2026-08-02', '2026-08-03')
    expect(extrema?.high.trade_date).toBe('2026-08-03')
    expect(extrema?.high.high).toBe(16)
    expect(extrema?.low.trade_date).toBe('2026-08-02')
    expect(extrema?.low.low).toBe(9)
  })

  it('detects upward and downward gaps and their first complete fills', () => {
    const gaps = detectPriceGaps([
      bar('2026-08-01', 10, 10, 8, 9),
      bar('2026-08-02', 12, 13, 12, 12.5),
      bar('2026-08-03', 12, 12.5, 10.5, 11),
      bar('2026-08-04', 7, 7.5, 6, 7),
      bar('2026-08-05', 7, 10.6, 6.5, 10),
    ])
    expect(gaps).toEqual([
      {
        direction: 'up', previousDate: '2026-08-01', startDate: '2026-08-02',
        fillDate: '2026-08-04', lower: 10, upper: 12,
      },
      {
        direction: 'down', previousDate: '2026-08-03', startDate: '2026-08-04',
        fillDate: '2026-08-05', lower: 7.5, upper: 10.5,
      },
    ])
  })

  it('keeps an unfilled gap open', () => {
    expect(detectPriceGaps([
      bar('2026-08-01', 10, 10, 8, 9),
      bar('2026-08-02', 12, 13, 12, 12.5),
      bar('2026-08-03', 13, 14, 11, 12),
    ])[0].fillDate).toBeUndefined()
  })

  it('does not classify continuous roll substitutions as ordinary gaps or fills', () => {
    const before = bar('2026-08-01', 10, 10, 8, 9)
    const roll = { ...bar('2026-08-02', 20, 21, 19, 20), roll_event: true }
    expect(detectPriceGaps([before, roll])).toEqual([])

    const openGap = detectPriceGaps([
      before,
      bar('2026-08-02', 12, 13, 12, 12.5),
      roll,
      bar('2026-08-04', 9, 10, 8, 9),
    ])[0]
    expect(openGap.fillDate).toBeUndefined()
  })

  it('limits the overlay to the latest four unfilled gaps', () => {
    const gaps: PriceGap[] = Array.from({ length: 6 }, (_, index) => ({
      direction: 'up' as const,
      previousDate: `2026-08-0${index + 1}`,
      startDate: `2026-08-0${index + 2}`,
      lower: 10 + index,
      upper: 11 + index,
    }))
    gaps[5].fillDate = '2026-08-08'
    expect(visibleUnfilledPriceGaps(gaps, '2026-08-10').map(gap => gap.startDate)).toEqual([
      '2026-08-03', '2026-08-04', '2026-08-05', '2026-08-06',
    ])
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
      rollEventCount: 0,
      comparable: true,
      elapsedDays: 30,
      kLineCount: 23,
    })
  })

  it('marks a range crossing continuous-contract rolls as non-comparable', () => {
    const first: DailyBar = {
      trade_date: '2026-07-01', open: 10, high: 11, low: 9, close: 10.5, volume: 100, source: 'test',
    }
    const last: DailyBar = {
      trade_date: '2026-07-31', open: 12, high: 13, low: 11, close: 12.5, volume: 100, source: 'test',
    }
    expect(createRangeMeasurement(first, last, 23, 1)).toMatchObject({
      rollEventCount: 1, comparable: false,
    })
  })
})
