// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  createTrendLine,
  deleteTrendLine,
  loadSymbolDrawings,
  saveTrendLine,
  subscribeSymbolDrawings,
} from './drawingStore'
import { chooseAnchor, renderDateForAnchor } from './trendLines'

describe('symbol drawing repository', () => {
  beforeEach(() => window.localStorage.clear())

  it('persists immutable data anchors separately for each symbol', () => {
    const drawing = createTrendLine('000001.SZ', [
      { date: '2026-07-01', price: 10, snap: 'low' },
      { date: '2026-07-31', price: 12, snap: 'high' },
    ], 'log', new Date('2026-08-05T00:00:00Z'), () => 'line-1')
    saveTrendLine(drawing)

    expect(loadSymbolDrawings('000001.SZ')).toEqual([drawing])
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
})
