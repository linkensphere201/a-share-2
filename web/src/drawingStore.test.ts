// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  createTrendLine,
  deleteTrendLine,
  drawingIdentityKey,
  loadSymbolDrawings,
  saveTrendLine,
  subscribeSymbolDrawings,
  type TrendLineAnchor,
} from './drawingStore'
import { chooseAnchor, extendLineToBounds, orientTrendLineAnchors, renderDateForAnchor, replaceTrendLineAnchor, translateTrendLineAnchors } from './trendLines'

describe('symbol drawing repository', () => {
  beforeEach(() => window.localStorage.clear())

  it('persists immutable data anchors separately for each symbol', () => {
    const drawing = createTrendLine('000001.SZ', [
      { date: '2026-07-01', price: 10, snap: 'low' },
      { date: '2026-07-31', price: 12, snap: 'high' },
    ], 'log', new Date('2026-08-05T00:00:00Z'), () => 'line-1')
    saveTrendLine(drawing)

    expect(loadSymbolDrawings('000001.SZ')).toEqual([drawing])
    expect(drawing.style.dash).toBe('dashed')
    expect(drawing.visible).toBe(true)
    expect(loadSymbolDrawings('600000.SH')).toEqual([])
  })

  it('notifies every mounted chart of the same symbol and supports deletion', () => {
    const listener = vi.fn()
    const unsubscribe = subscribeSymbolDrawings('000001.SZ', listener)
    const drawing = createTrendLine('000001.SZ', [
      { date: '2026-07-01', price: 10, snap: 'free' },
      { date: '2026-07-31', price: 12, snap: 'free' },
    ], 'normal', new Date('2026-08-05T00:00:00Z'), () => 'line-1')

    saveTrendLine(drawing)
    deleteTrendLine('000001.SZ', drawing.id)

    expect(listener).toHaveBeenCalledTimes(2)
    expect(loadSymbolDrawings('000001.SZ')).toEqual([])
    unsubscribe()
  })

  it('preserves old solid lines and defaults their missing visibility to visible', () => {
    const drawing = createTrendLine('000001.SZ', [
      { date: '2026-07-01', price: 10, snap: 'free' },
      { date: '2026-07-31', price: 12, snap: 'free' },
    ], 'normal', new Date('2026-08-05T00:00:00Z'), () => 'line-legacy')
    const legacy = { ...drawing, style: { ...drawing.style, dash: 'solid' } }
    delete (legacy as Partial<typeof legacy>).visible
    window.localStorage.setItem('stock-harness.drawings.v1', JSON.stringify({
      version: 1,
      symbols: { '000001.SZ': [legacy] },
    }))

    expect(loadSymbolDrawings('000001.SZ')[0]).toMatchObject({
      visible: true,
      style: { dash: 'solid' },
    })
  })

  it('round-trips hidden state, color, and extended dash patterns', () => {
    const drawing = createTrendLine('000001.SZ', [
      { date: '2026-07-01', price: 10, snap: 'free' },
      { date: '2026-07-31', price: 12, snap: 'free' },
    ], 'normal', new Date('2026-08-05T00:00:00Z'), () => 'line-style')
    saveTrendLine({
      ...drawing,
      visible: false,
      style: { ...drawing.style, color: '#57a7d9', dash: 'dash-dot' },
    })

    expect(loadSymbolDrawings('000001.SZ')[0]).toMatchObject({
      visible: false,
      style: { color: '#57a7d9', dash: 'dash-dot' },
    })
  })

  it('isolates continuous futures drawings by price basis and rule version', () => {
    const raw = {
      symbol: 'FUTCONT:SHFE:CU:MAIN:raw',
      instrumentKind: 'futures-continuous',
      priceBasis: 'raw',
      ruleVersion: 'tushare-fut-mapping-v1',
    }
    const adjusted = {
      ...raw,
      symbol: 'FUTCONT:SHFE:CU:MAIN:backward-ratio',
      priceBasis: 'backward-ratio',
    }
    const upgraded = { ...raw, ruleVersion: 'tushare-fut-mapping-v2' }
    const drawing = createTrendLine(raw, [
      { date: '2026-07-01', price: 78_000, snap: 'low' },
      { date: '2026-07-31', price: 81_000, snap: 'high' },
    ], 'normal', new Date('2026-08-05T00:00:00Z'), () => 'cu-line')
    saveTrendLine(drawing)

    expect(loadSymbolDrawings(raw)).toEqual([drawing])
    expect(loadSymbolDrawings(adjusted)).toEqual([])
    expect(loadSymbolDrawings(upgraded)).toEqual([])
    expect(drawingIdentityKey(raw)).not.toBe(drawingIdentityKey(upgraded))
  })

  it('quarantines legacy futures drawings instead of attaching them implicitly', () => {
    const legacy = {
      ...createTrendLine('FUTCONT:SHFE:CU:MAIN:raw', [
        { date: '2026-07-01', price: 78_000, snap: 'low' },
        { date: '2026-07-31', price: 81_000, snap: 'high' },
      ], 'normal', new Date('2026-08-05T00:00:00Z'), () => 'legacy-line'),
    }
    delete (legacy as Partial<typeof legacy>).identityKey
    window.localStorage.setItem('stock-harness.drawings.v1', JSON.stringify({
      version: 1,
      symbols: { 'FUTCONT:SHFE:CU:MAIN:raw': [legacy] },
    }))

    expect(loadSymbolDrawings({
      symbol: 'FUTCONT:SHFE:CU:MAIN:raw',
      instrumentKind: 'futures-continuous',
      priceBasis: 'raw',
      ruleVersion: 'tushare-fut-mapping-v1',
    })).toEqual([])
    const migrated = JSON.parse(window.localStorage.getItem('stock-harness.drawings.v2') ?? '{}')
    expect(migrated.legacyFutures['FUTCONT:SHFE:CU:MAIN:raw']).toHaveLength(1)
  })

  it('rejects continuous drawing creation until the full identity is known', () => {
    expect(() => createTrendLine({
      symbol: 'FUTCONT:SHFE:CU:MAIN:raw',
      instrumentKind: 'futures-continuous',
      priceBasis: 'raw',
    }, [
      { date: '2026-07-01', price: 78_000, snap: 'low' },
      { date: '2026-07-31', price: 81_000, snap: 'high' },
    ], 'normal')).toThrow('identity is incomplete')
  })
})

describe('trend-line anchors', () => {
  it('snaps only to a nearby high or low candidate', () => {
    const fallback = { date: '2026-08-05', price: 11, snap: 'free' as const }
    expect(chooseAnchor(100, 50, fallback, [
      { date: '2026-08-05', price: 12, snap: 'high', x: 104, y: 54 },
      { date: '2026-08-05', price: 9, snap: 'low', x: 104, y: 90 },
    ])).toEqual({ date: '2026-08-05', price: 12, snap: 'high' })
    expect(chooseAnchor(10, 10, fallback, [])).toBe(fallback)
  })

  it('maps an immutable daily date to its current LOD bucket date', () => {
    const periods = [
      { period_start: '2026-08-01', trade_date: '2026-08-04' },
      { period_start: '2026-08-05', trade_date: '2026-08-08' },
    ]
    expect(renderDateForAnchor('2026-08-02', periods)).toBe('2026-08-04')
    expect(renderDateForAnchor('2026-08-07', periods)).toBe('2026-08-08')
  })

  it('extends a two-anchor segment to both price-pane edges', () => {
    expect(extendLineToBounds({ x1: 25, y1: 75, x2: 75, y2: 25 }, 100, 100)).toEqual({
      x1: 0, y1: 100, x2: 100, y2: 0,
    })
    expect(extendLineToBounds({ x1: 50, y1: 25, x2: 50, y2: 75 }, 100, 100)).toEqual({
      x1: 50, y1: 0, x2: 50, y2: 100,
    })
  })

  it('moves both anchors together by trading date and linear price delta', () => {
    expect(translateTrendLineAnchors([
      { date: '2026-08-02', price: 10, snap: 'low' },
      { date: '2026-08-04', price: 12, snap: 'high' },
    ], ['2026-08-01', '2026-08-02', '2026-08-03', '2026-08-04', '2026-08-05'], 1, 11, 13, 'normal')).toEqual([
      { date: '2026-08-03', price: 12, snap: 'free' },
      { date: '2026-08-05', price: 14, snap: 'free' },
    ])
  })

  it('replaces only the dragged endpoint and preserves its resolved snap state', () => {
    const anchors = [
      { date: '2026-08-02', price: 10, snap: 'low' as const },
      { date: '2026-08-04', price: 12, snap: 'high' as const },
    ] as const

    expect(replaceTrendLineAnchor([...anchors], 1, {
      date: '2026-08-05', price: 13.5, snap: 'free',
    })).toEqual([
      { date: '2026-08-02', price: 10, snap: 'low' },
      { date: '2026-08-05', price: 13.5, snap: 'free' },
    ])
  })

  it('orients a line around its first anchor and clears stale snap metadata', () => {
    const anchors = [
      { date: '2026-08-02', price: 10, snap: 'low' as const },
      { date: '2026-08-04', price: 12, snap: 'high' as const },
    ] as [TrendLineAnchor, TrendLineAnchor]

    expect(orientTrendLineAnchors(anchors, 'horizontal')).toEqual([
      { date: '2026-08-02', price: 10, snap: 'low' },
      { date: '2026-08-04', price: 10, snap: 'free' },
    ])
    expect(orientTrendLineAnchors(anchors, 'vertical')).toEqual([
      { date: '2026-08-02', price: 10, snap: 'low' },
      { date: '2026-08-02', price: 12, snap: 'free' },
    ])
  })

  it('preserves visual slope on logarithmic movement and clamps dates as a pair', () => {
    expect(translateTrendLineAnchors([
      { date: '2026-08-02', price: 10, snap: 'free' },
      { date: '2026-08-04', price: 20, snap: 'free' },
    ], ['2026-08-01', '2026-08-02', '2026-08-03', '2026-08-04', '2026-08-05'], 3, 10, 15, 'log')).toEqual([
      { date: '2026-08-03', price: 15, snap: 'free' },
      { date: '2026-08-05', price: 30, snap: 'free' },
    ])
  })
})
