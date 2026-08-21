// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  createTrendLine,
  deleteTrendLine,
  drawingIdentityKey,
  listDrawingMigrationCandidates,
  loadSymbolDrawings,
  saveTrendLine,
  resolveDrawingMigration,
  subscribeSymbolDrawings,
  type TrendLineAnchor,
} from './drawingStore'
import { chooseAnchor, extendLineToBounds, orientTrendLineAnchors, renderDateForAnchor, replaceTrendLineAnchor, translateTrendLineAnchors } from './trendLines'
import { aggregateBars, mergeProvisionalBar, type DailyBar } from './chartData'

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

  it('migrates drawings only across an explicitly accepted same-basis rule change', () => {
    const oldTarget = {
      symbol: 'FUTCONT:SHFE:CU:MAIN:raw',
      instrumentKind: 'futures-continuous',
      priceBasis: 'raw',
      ruleVersion: 'mapping-v1',
    }
    const currentTarget = { ...oldTarget, ruleVersion: 'mapping-v2' }
    saveTrendLine(createTrendLine(oldTarget, [
      { date: '2026-07-01', price: 78_000, snap: 'low' },
      { date: '2026-07-31', price: 81_000, snap: 'high' },
    ], 'normal', new Date('2026-08-05T00:00:00Z'), () => 'old-rule-line'))

    const candidate = listDrawingMigrationCandidates(currentTarget)[0]
    expect(candidate).toMatchObject({
      kind: 'same-basis-rule-change', canMigrate: true, drawingCount: 1,
      sourceRuleVersion: 'mapping-v1',
    })
    resolveDrawingMigration(
      currentTarget, candidate.id, 'migrate', window.localStorage,
      new Date('2026-08-06T00:00:00Z'),
    )

    expect(loadSymbolDrawings(currentTarget)[0]).toMatchObject({
      identityKey: drawingIdentityKey(currentTarget),
      priceBasis: 'raw', ruleVersion: 'mapping-v2',
      anchors: loadSymbolDrawings(oldTarget)[0].anchors,
    })
    expect(loadSymbolDrawings(oldTarget)).toHaveLength(1)
    expect(listDrawingMigrationCandidates(currentTarget)).toEqual([])
  })

  it('rejects price-basis migration and records an explicit isolation decision', () => {
    const raw = {
      symbol: 'FUTCONT:SHFE:CU:MAIN:raw', instrumentKind: 'futures-continuous',
      priceBasis: 'raw', ruleVersion: 'mapping-v1',
    }
    const adjusted = {
      symbol: 'FUTCONT:SHFE:CU:MAIN:backward-ratio', instrumentKind: 'futures-continuous',
      priceBasis: 'backward-ratio', ruleVersion: 'mapping-v1',
    }
    saveTrendLine(createTrendLine(raw, [
      { date: '2026-07-01', price: 78_000, snap: 'low' },
      { date: '2026-07-31', price: 81_000, snap: 'high' },
    ], 'normal'))
    const candidate = listDrawingMigrationCandidates(adjusted)[0]
    expect(candidate).toMatchObject({ kind: 'different-price-basis', canMigrate: false })
    expect(() => resolveDrawingMigration(adjusted, candidate.id, 'migrate')).toThrow(
      'identical price basis',
    )

    resolveDrawingMigration(adjusted, candidate.id, 'reject')
    expect(loadSymbolDrawings(adjusted)).toEqual([])
    expect(loadSymbolDrawings(raw)).toHaveLength(1)
    expect(listDrawingMigrationCandidates(adjusted)).toEqual([])
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

    const target = {
      symbol: 'FUTCONT:SHFE:CU:MAIN:raw',
      instrumentKind: 'futures-continuous',
      priceBasis: 'raw',
      ruleVersion: 'tushare-fut-mapping-v1',
    }
    expect(loadSymbolDrawings(target)).toEqual([])
    expect(listDrawingMigrationCandidates(target)[0]).toMatchObject({
      kind: 'legacy-unknown', canMigrate: false, drawingCount: 1,
    })
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

  it('persists the complete futures trend-line editing workflow', () => {
    const target = {
      symbol: 'FUT:SHFE:CU:202609',
      instrumentKind: 'futures-contract',
    }
    const created = createTrendLine(target, [
      { date: '2026-07-01', price: 78_000, snap: 'low' },
      { date: '2026-07-31', price: 81_000, snap: 'high' },
    ], 'log', new Date('2026-08-05T00:00:00Z'), () => 'cu-edit')
    const endpointEdited = replaceTrendLineAnchor(created.anchors, 1, {
      date: '2026-08-01', price: 82_000, snap: 'high',
    })
    const moved = translateTrendLineAnchors(
      endpointEdited,
      ['2026-07-01', '2026-07-02', '2026-07-31', '2026-08-01', '2026-08-04'],
      1,
      78_000,
      79_000,
      'log',
    )
    const horizontal = orientTrendLineAnchors(moved, 'horizontal')
    saveTrendLine({
      ...created,
      anchors: horizontal,
      visible: false,
      style: { ...created.style, color: '#57a7d9', dash: 'long-dashed' },
      updatedAt: '2026-08-06T00:00:00.000Z',
    })

    const restored = loadSymbolDrawings(target)[0]
    expect(restored).toMatchObject({
      symbol: target.symbol,
      instrumentKind: 'futures-contract',
      coordinateMode: 'log',
      visible: false,
      style: { color: '#57a7d9', dash: 'long-dashed' },
    })
    expect(restored.anchors[0].price).toBe(restored.anchors[1].price)
    expect(extendLineToBounds(
      { x1: 25, y1: 50, x2: 75, y2: 50 }, 100, 100,
    )).toEqual({ x1: 0, y1: 50, x2: 100, y2: 50 })
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

  it('keeps futures data anchors immutable across LOD and provisional replacement', () => {
    const target = {
      symbol: 'FUTCONT:SHFE:CU:MAIN:raw',
      instrumentKind: 'futures-continuous',
      priceBasis: 'raw',
      ruleVersion: 'tushare-fut-mapping-v1',
    }
    const drawing = createTrendLine(target, [
      { date: '2026-08-01', price: 78_000, snap: 'low' },
      { date: '2026-08-03', price: 81_000, snap: 'high' },
    ], 'log', new Date('2026-08-05T00:00:00Z'), () => 'stable-line')
    saveTrendLine(drawing)
    const bars: DailyBar[] = [
      daily('2026-08-01', 78_000), daily('2026-08-02', 79_000),
      daily('2026-08-03', 81_000), daily('2026-08-04', 80_000),
    ]
    const lod = aggregateBars(bars, 2)
    expect(renderDateForAnchor(drawing.anchors[0].date, lod)).toBe('2026-08-02')
    expect(renderDateForAnchor(drawing.anchors[1].date, lod)).toBe('2026-08-04')

    const withProvisional = mergeProvisionalBar(bars, {
      ...daily('2026-08-05', 82_000), bar_state: 'intraday',
    })
    const canonical = withProvisional.map(item => item.trade_date === '2026-08-05'
      ? { ...item, close: 81_500, bar_state: 'final' as const }
      : item)
    expect(canonical.at(-1)?.bar_state).toBe('final')
    expect(loadSymbolDrawings(target)[0].anchors).toEqual(drawing.anchors)
  })
})

function daily(trade_date: string, close: number): DailyBar {
  return {
    trade_date,
    open: close - 100,
    high: close + 200,
    low: close - 200,
    close,
    volume: 1_000,
    source: 'test',
    bar_state: 'final',
  }
}
