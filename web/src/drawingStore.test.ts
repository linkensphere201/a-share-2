// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  createTrendLine,
  deleteTrendLine,
  loadSymbolDrawings,
  saveTrendLine,
  subscribeSymbolDrawings,
} from './drawingStore'
import { chooseAnchor, extendLineToBounds, renderDateForAnchor } from './trendLines'

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
})
