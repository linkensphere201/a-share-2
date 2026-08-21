// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createTrendLine, saveTrendLine } from './drawingStore'
import { createDefaultWorkspace } from './workspace'
import { buildWorkspaceContext, publishWorkspaceContext } from './workspaceContext'

describe('active workspace context', () => {
  beforeEach(() => {
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('publishes active windows, relationships, resolved symbols, chart state, and symbol drawings', () => {
    const group = createDefaultWorkspace().groups[0]
    const chart = group.windows.find(window => window.type === 'chart')
    const list = group.windows.find(window => window.type === 'instrument-list')
    if (!chart || chart.type !== 'chart' || !list || list.type !== 'instrument-list') {
      throw new Error('expected default chart and list')
    }
    chart.chart.priceMode = 'log'
    chart.chart.visibleRange = { from: '2026-01-05', to: '2026-08-05' }
    saveTrendLine(createTrendLine(chart.instrument.symbol, [
      { date: '2026-01-05', price: 10, snap: 'low' },
      { date: '2026-08-05', price: 15, snap: 'high' },
    ], 'normal', new Date('2026-08-06T00:00:00Z'), () => 'line-1'))

    const context = buildWorkspaceContext(
      group,
      { [list.id]: ['000001.SZ', '600519.SH'] },
      new Date('2026-08-06T01:02:03Z'),
    )

    expect(context).toMatchObject({
      schema_version: '1.0',
      published_at: '2026-08-06T01:02:03.000Z',
      active_group_id: group.id,
      focused_window_id: group.focusedWindowId,
    })
    expect(context.referenced_symbols).toContain('000001.SZ')
    expect(context.attachments).toHaveLength(1)
    expect(context.windows.find(window => window.type === 'chart')).toMatchObject({
      chart: {
        coordinate_mode: 'log',
        visible_start: '2026-01-05',
        visible_end: '2026-08-05',
        volume_visible: true,
        indicator: 'macd',
      },
    })
    expect(context.windows.find(window => window.type === 'instrument-list')).toMatchObject({
      resolved_symbols: ['000001.SZ', '600519.SH'],
    })
    expect(context.drawings_by_symbol[chart.instrument.symbol][0]).toMatchObject({
      id: 'line-1',
      coordinate_mode: 'normal',
      anchors: [{ snap: 'low' }, { snap: 'high' }],
    })
  })

  it('posts the snapshot to the bounded workspace endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchMock)
    const group = createDefaultWorkspace().groups[0]
    const context = buildWorkspaceContext(group)

    await publishWorkspaceContext(context)

    expect(fetchMock).toHaveBeenCalledWith('/api/workspace-context', expect.objectContaining({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    }))
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).active_group_id).toBe(group.id)
  })

  it('publishes exact continuous futures identity and identity-owned drawings', () => {
    const group = createDefaultWorkspace().groups[0]
    const chart = group.windows.find(window => window.type === 'chart')
    if (!chart || chart.type !== 'chart') throw new Error('expected chart')
    chart.instrument = {
      symbol: 'FUTCONT:SHFE:CU:MAIN:raw',
      name: 'Copper main',
      kind: 'futures-continuous',
      exchange: 'SHFE',
      product_code: 'CU',
      series_kind: 'main',
      series_variant: 'MAIN',
      price_basis: 'raw',
      rule_version: 'mapping-v1',
      rows: 20,
    }
    saveTrendLine(createTrendLine({
      symbol: chart.instrument.symbol,
      instrumentKind: chart.instrument.kind,
      priceBasis: chart.instrument.price_basis,
      ruleVersion: chart.instrument.rule_version,
    }, [
      { date: '2026-07-01', price: 100, snap: 'low' },
      { date: '2026-08-01', price: 110, snap: 'high' },
    ], 'normal', new Date('2026-08-06T00:00:00Z'), () => 'futures-line'))

    const context = buildWorkspaceContext(group)
    const publishedChart = context.windows.find(window => window.type === 'chart')

    expect(publishedChart).toMatchObject({
      instrument: {
        symbol: chart.instrument.symbol,
        kind: 'futures-continuous',
        exchange: 'SHFE',
        product_code: 'CU',
        price_basis: 'raw',
        rule_version: 'mapping-v1',
      },
    })
    expect(context.referenced_symbols).toContain(chart.instrument.symbol)
    expect(context.drawings_by_symbol[chart.instrument.symbol][0].id).toBe('futures-line')
  })
})
